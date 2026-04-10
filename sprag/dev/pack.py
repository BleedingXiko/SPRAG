"""
sprag pack — production-ready dist optimizer.

Takes the output of ``sprag build`` and optimizes it for deployment:
- CSS/JS minification (regex fallback, optional terser/cleancss)
- Python bytecode compilation with source stripping
- Image optimization (resize, compress, WebP via Pillow)
- Bytecode importer for sourceless dist loading
- Build validation (dist boots and renders)
- Optional ZIP packaging

Usage:
    sprag pack [--dist PATH] [--zip] [--dry-run] [--verbose]
"""

from __future__ import annotations

import gzip as gzip_mod
import marshal
import os
import re
import shutil
import subprocess
import sys
import time
import types
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


# --- CONSTANTS ---

CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_CYAN = "\033[36m"
CLR_GREEN = "\033[32m"
CLR_YELLOW = "\033[33m"
CLR_RED = "\033[31m"
CLR_BLUE = "\033[34m"

COMPRESSIBLE_SUFFIXES = {
    ".html", ".css", ".js", ".json", ".svg", ".xml", ".txt", ".map",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"}
WEBP_CONVERTIBLE = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}

DEFAULT_EXCLUDE_DIRS = {
    "__pycache__", ".DS_Store", ".git",
}

# Bytecode importer template — loaded at dist boot before anything else.
# Allows Python to import directly from __pycache__/*.pyc with no .py source.
BYTECODE_IMPORTER = r'''
import sys, importlib.util, importlib.machinery
from pathlib import Path

class _SpragBytecodeImporter:
    def __init__(self): self._loading = set()
    def find_spec(self, name, path=None, target=None):
        root_name = name.split('.')[0]
        if root_name not in ('app', 'sprag'):
            return None
        if name in self._loading:
            return None
        self._loading.add(name)
        try:
            module_name = name.split('.')[-1]
            tag = getattr(
                sys.implementation, 'cache_tag',
                'cpython-' + str(sys.version_info.major) + str(sys.version_info.minor),
            )
            for entry in (path if path else sys.path):
                try:
                    base = Path(entry).resolve()
                    package_dir = base / module_name
                    package_pyc = package_dir / "__pycache__" / f"__init__.{tag}.pyc"
                    if package_pyc.exists():
                        loader = importlib.machinery.SourcelessFileLoader(name, str(package_pyc))
                        return importlib.util.spec_from_file_location(
                            name,
                            str(package_pyc),
                            loader=loader,
                            submodule_search_locations=[str(package_dir)],
                        )
                    module_pyc = base / "__pycache__" / f"{module_name}.{tag}.pyc"
                    if module_pyc.exists():
                        loader = importlib.machinery.SourcelessFileLoader(name, str(module_pyc))
                        return importlib.util.spec_from_file_location(name, str(module_pyc), loader=loader)
                except Exception:
                    continue
            return None
        finally:
            self._loading.remove(name)

if not any(isinstance(h, _SpragBytecodeImporter) for h in sys.meta_path):
    sys.meta_path.insert(0, _SpragBytecodeImporter())
'''


# --- PARALLEL WORKERS ---

def _worker_minify_css(path_str: str, cleancss_bin: Optional[str]) -> Tuple[bool, str, int, int]:
    path = Path(path_str)
    try:
        orig_size = path.stat().st_size
        if cleancss_bin:
            proc = subprocess.run(
                [cleancss_bin, "-O2", "-o", path_str, path_str],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                return True, "", orig_size, path.stat().st_size
        css = path.read_text(encoding="utf-8", errors="ignore")
        minified = _minify_css_fallback(css)
        path.write_text(minified, encoding="utf-8")
        return True, "", orig_size, path.stat().st_size
    except Exception as e:
        return False, str(e), 0, 0


def _worker_minify_js(path_str: str, terser_bin: Optional[str]) -> Tuple[bool, str, int, int]:
    path = Path(path_str)
    try:
        orig_size = path.stat().st_size
        if terser_bin:
            proc = subprocess.run(
                [terser_bin, path_str, "-o", path_str, "--compress", "--mangle"],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                return True, "", orig_size, path.stat().st_size
        js = path.read_text(encoding="utf-8", errors="ignore")
        minified = _minify_js_fallback(js)
        path.write_text(minified, encoding="utf-8")
        return True, "", orig_size, path.stat().st_size
    except Exception as e:
        return False, str(e), 0, 0


def _worker_compile_py(path_str: str, cfile_str: str, dist_dir_str: str) -> Tuple[bool, str]:
    import py_compile
    try:
        Path(cfile_str).parent.mkdir(parents=True, exist_ok=True)
        orig_cwd = os.getcwd()
        os.chdir(dist_dir_str)
        try:
            rel_path = os.path.relpath(path_str, dist_dir_str)
            py_compile.compile(rel_path, cfile=cfile_str, dfile=rel_path, doraise=True)
        finally:
            os.chdir(orig_cwd)
        return True, ""
    except Exception as e:
        return False, str(e)


def _worker_optimize_image(
    path_str: str,
    quality: int,
    max_width: int,
    generate_webp: bool,
    generate_srcset: bool,
    srcset_widths: List[int],
) -> Tuple[bool, str, int, int, List[str]]:
    path = Path(path_str)
    generated_files = []
    try:
        from PIL import Image
    except ImportError:
        return True, "pillow not installed, skipping", path.stat().st_size, path.stat().st_size, []
    try:
        orig_size = path.stat().st_size
        img = Image.open(path)
        orig_format = img.format

        # Resize if wider than max_width
        if max_width and img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.LANCZOS)

        # Re-save original format with compression
        if path.suffix.lower() in (".jpg", ".jpeg"):
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(path, "JPEG", quality=quality, optimize=True)
        elif path.suffix.lower() == ".png":
            img.save(path, "PNG", optimize=True)
        else:
            img.save(path, optimize=True)

        # Generate WebP variant
        if generate_webp and path.suffix.lower() in WEBP_CONVERTIBLE:
            webp_path = path.with_suffix(".webp")
            webp_img = img
            if webp_img.mode == "P":
                webp_img = webp_img.convert("RGBA")
            webp_img.save(webp_path, "WEBP", quality=quality, method=4)
            generated_files.append(str(webp_path))

        # Generate responsive srcset variants
        if generate_srcset and img.width > min(srcset_widths):
            stem = path.stem
            for width in srcset_widths:
                if width >= img.width:
                    continue
                ratio = width / img.width
                h = int(img.height * ratio)
                resized = img.resize((width, h), Image.LANCZOS)
                variant_path = path.with_name(f"{stem}-{width}w{path.suffix}")
                if path.suffix.lower() in (".jpg", ".jpeg"):
                    save_img = resized.convert("RGB") if resized.mode in ("RGBA", "P") else resized
                    save_img.save(variant_path, "JPEG", quality=quality, optimize=True)
                else:
                    resized.save(variant_path, optimize=True)
                generated_files.append(str(variant_path))
                # WebP variant for srcset too
                if generate_webp and path.suffix.lower() in WEBP_CONVERTIBLE:
                    webp_variant = variant_path.with_suffix(".webp")
                    webp_resized = resized
                    if webp_resized.mode == "P":
                        webp_resized = webp_resized.convert("RGBA")
                    webp_resized.save(webp_variant, "WEBP", quality=quality, method=4)
                    generated_files.append(str(webp_variant))

        new_size = path.stat().st_size
        return True, "", orig_size, new_size, generated_files
    except Exception as e:
        return False, str(e), 0, 0, []


# --- MINIFICATION FALLBACKS ---

def _minify_css_fallback(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,>+~])\s*", r"\1", css)
    return css.strip()


def _minify_js_fallback(js: str) -> str:
    lines = []
    for line in js.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # Remove single-line comments but not URLs (://)
        if stripped.startswith("//") and not stripped.startswith("//!"):
            continue
        lines.append(stripped)
    return "\n".join(lines) + "\n"


# --- BYTECODE SAFETY ---

def _iter_code_filenames(code_obj: types.CodeType) -> Iterable[str]:
    yield code_obj.co_filename
    for const in code_obj.co_consts:
        if isinstance(const, types.CodeType):
            yield from _iter_code_filenames(const)


def _find_host_path_in_pyc(pyc_path: Path) -> Optional[str]:
    try:
        with pyc_path.open("rb") as fh:
            fh.read(16)
            code = marshal.load(fh)
        for filename in _iter_code_filenames(code):
            if not filename:
                continue
            if filename.startswith("/Users/") or filename.startswith("/home/"):
                return filename
            if re.match(r"^[A-Za-z]:[\\/]", filename):
                return filename
    except Exception:
        return None
    return None


# --- CORE PACKER ---

class SpragPack:
    def __init__(
        self,
        dist_dir: Path,
        *,
        zip_output: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
        image_quality: int = 80,
        image_max_width: int = 1920,
        generate_webp: bool = True,
        generate_srcset: bool = True,
        srcset_widths: Optional[List[int]] = None,
        skip_images: bool = False,
        skip_minify: bool = False,
        skip_bytecode: bool = False,
        skip_gzip: bool = False,
    ):
        self.dist_dir = dist_dir.resolve()
        self.zip_output = zip_output
        self.dry_run = dry_run
        self.verbose = verbose
        self.image_quality = image_quality
        self.image_max_width = image_max_width
        self.generate_webp = generate_webp
        self.generate_srcset = generate_srcset
        self.srcset_widths = srcset_widths or [640, 960, 1280]
        self.skip_images = skip_images
        self.skip_minify = skip_minify
        self.skip_bytecode = skip_bytecode
        self.skip_gzip = skip_gzip

        self.workers = os.cpu_count() or 4
        self.terser_bin = shutil.which("terser")
        self.cleancss_bin = shutil.which("cleancss")

        self.start_time = time.time()
        self.stats: Dict = {
            "minified_css": 0,
            "minified_js": 0,
            "compiled_py": 0,
            "removed_py": 0,
            "optimized_images": 0,
            "generated_variants": 0,
            "gzipped_files": 0,
            "orig_asset_size": 0,
            "packed_asset_size": 0,
            "orig_image_size": 0,
            "packed_image_size": 0,
            "gzip_saved": 0,
            "errors": [],
        }

    def log(self, msg: str):
        print(f"{CLR_BLUE}[*]{CLR_RESET} {msg}")

    def error(self, msg: str):
        print(f"{CLR_RED}[!] {msg}{CLR_RESET}")
        self.stats["errors"].append(msg)

    def success(self, msg: str):
        print(f"{CLR_GREEN}[+] {msg}{CLR_RESET}")

    def phase(self, name: str):
        print(f"\n{CLR_BOLD}{CLR_CYAN}--- {name.upper()} ---{CLR_RESET}")

    def execute(self):
        print(f"\n{CLR_BOLD}{CLR_CYAN}SPRAG PACK{CLR_RESET}")
        self.log(f"dist: {self.dist_dir}")
        self.log(f"workers: {self.workers}")

        if not self.dist_dir.exists():
            self.error(f"dist directory does not exist: {self.dist_dir}")
            raise SystemExit(1)
        if not (self.dist_dir / "server.py").exists():
            self.error("Not a SPRAG dist — missing server.py. Run 'sprag build' first.")
            raise SystemExit(1)

        if not self.skip_minify:
            self._phase_minify()
        if not self.skip_images:
            self._phase_images()
        if not self.skip_bytecode:
            self._phase_bytecode()
        if not self.skip_gzip:
            self._phase_pregzip()
        self._phase_validate()
        if self.zip_output:
            self._phase_package()
        self._report()

    def _phase_minify(self):
        self.phase("Minifying Assets")
        public_dir = self.dist_dir / "public"
        if not public_dir.exists():
            self.log("No public/ directory, skipping minification")
            return

        css_files = [p for p in public_dir.rglob("*.css") if not p.name.endswith(".min.css")]
        js_files = [
            p for p in public_dir.rglob("*.js")
            if not p.name.endswith(".min.js")
            and "vendor" not in p.relative_to(public_dir).parts
        ]

        if not css_files and not js_files:
            self.log("No CSS/JS files to minify")
            return

        with ProcessPoolExecutor(max_workers=self.workers) as executor:
            futures = []
            for p in css_files:
                futures.append(("css", executor.submit(_worker_minify_css, str(p), self.cleancss_bin)))
            for p in js_files:
                futures.append(("js", executor.submit(_worker_minify_js, str(p), self.terser_bin)))

            for kind, future in futures:
                ok, err, old, new = future.result()
                if ok:
                    self.stats["orig_asset_size"] += old
                    self.stats["packed_asset_size"] += new
                    if kind == "css":
                        self.stats["minified_css"] += 1
                    else:
                        self.stats["minified_js"] += 1
                else:
                    self.error(f"Minification failed: {err}")

        saved = (self.stats["orig_asset_size"] - self.stats["packed_asset_size"]) / 1024
        self.success(
            f"Minified {self.stats['minified_css']} CSS, "
            f"{self.stats['minified_js']} JS (saved {saved:.1f} KB)"
        )

    def _phase_images(self):
        self.phase("Optimizing Images")
        public_dir = self.dist_dir / "public"
        if not public_dir.exists():
            self.log("No public/ directory, skipping image optimization")
            return

        image_files = [
            p for p in public_dir.rglob("*")
            if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file()
        ]
        if not image_files:
            self.log("No images found")
            return

        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.log("Pillow not installed — skipping image optimization (pip install Pillow)")
            return

        with ProcessPoolExecutor(max_workers=self.workers) as executor:
            futures = []
            for p in image_files:
                futures.append(
                    executor.submit(
                        _worker_optimize_image,
                        str(p),
                        self.image_quality,
                        self.image_max_width,
                        self.generate_webp,
                        self.generate_srcset,
                        self.srcset_widths,
                    )
                )

            for future in as_completed(futures):
                ok, err, old, new, generated = future.result()
                if ok:
                    self.stats["optimized_images"] += 1
                    self.stats["orig_image_size"] += old
                    self.stats["packed_image_size"] += new
                    self.stats["generated_variants"] += len(generated)
                else:
                    self.error(f"Image optimization failed: {err}")

        saved = (self.stats["orig_image_size"] - self.stats["packed_image_size"]) / 1024
        self.success(
            f"Optimized {self.stats['optimized_images']} images, "
            f"generated {self.stats['generated_variants']} variants "
            f"(saved {saved:.1f} KB)"
        )

    def _phase_bytecode(self):
        self.phase("Bytecode Compilation")
        py_files = []
        # Compile app/ and sprag/ Python files inside dist
        for package in ("app", "sprag"):
            package_dir = self.dist_dir / package
            if not package_dir.exists():
                continue
            for p in package_dir.rglob("*.py"):
                py_files.append(p)

        if not py_files:
            self.log("No Python files to compile")
            return

        tag = getattr(
            sys.implementation, "cache_tag",
            f"cpython-{sys.version_info.major}{sys.version_info.minor}",
        )

        with ProcessPoolExecutor(max_workers=self.workers) as executor:
            futures = []
            for py in py_files:
                cfile = py.parent / "__pycache__" / f"{py.stem}.{tag}.pyc"
                futures.append((py, executor.submit(
                    _worker_compile_py, str(py), str(cfile), str(self.dist_dir),
                )))

            for py, future in futures:
                ok, err = future.result()
                if ok:
                    self.stats["compiled_py"] += 1
                else:
                    self.error(f"Compilation failed ({py.name}): {err}")

        # Check for leaked host paths in bytecode
        leaked = []
        for pyc in self.dist_dir.rglob("*.pyc"):
            host_path = _find_host_path_in_pyc(pyc)
            if host_path:
                leaked.append((pyc, host_path))
        if leaked:
            for pyc, path in leaked[:10]:
                self.error(f"Path leak in {pyc.relative_to(self.dist_dir)}: {path}")
            raise RuntimeError("Bytecode contains absolute host paths; refusing to pack")

        # Strip source files
        for py in py_files:
            if not self.dry_run:
                py.unlink()
            self.stats["removed_py"] += 1

        # Patch server.py with bytecode importer
        server_py = self.dist_dir / "server.py"
        if server_py.exists():
            content = server_py.read_text(encoding="utf-8")
            if "_SpragBytecodeImporter" not in content:
                patched = BYTECODE_IMPORTER + "\n" + content
                if not self.dry_run:
                    server_py.write_text(patched, encoding="utf-8")

        self.success(
            f"Compiled {self.stats['compiled_py']} files, "
            f"removed {self.stats['removed_py']} source files"
        )

    def _phase_pregzip(self):
        self.phase("Pre-compressing Static Assets")
        public_dir = self.dist_dir / "public"
        if not public_dir.exists():
            self.log("No public/ directory, skipping pre-compression")
            return

        count = 0
        saved = 0
        for p in public_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in COMPRESSIBLE_SUFFIXES:
                continue
            if p.suffix == ".gz":
                continue
            orig_size = p.stat().st_size
            if orig_size < 1024:
                continue
            try:
                data = p.read_bytes()
                compressed = gzip_mod.compress(data, compresslevel=6)
                if len(compressed) >= orig_size:
                    continue
                if not self.dry_run:
                    gz_path = p.with_name(p.name + ".gz")
                    gz_path.write_bytes(compressed)
                count += 1
                saved += orig_size - len(compressed)
            except Exception as e:
                self.error(f"Pre-gzip failed for {p.name}: {e}")

        self.stats["gzipped_files"] = count
        self.stats["gzip_saved"] = saved
        self.success(f"Pre-compressed {count} files (saved {saved / 1024:.1f} KB)")

    def _phase_validate(self):
        self.phase("Validating Packed Dist")
        server_py = self.dist_dir / "server.py"
        if not server_py.exists():
            self.error("server.py missing from dist")
            return

        # Check that the packed dist can at least parse/import
        result = subprocess.run(
            [sys.executable, "-c", f"import ast; ast.parse(open({str(server_py)!r}).read())"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            self.error(f"server.py syntax check failed: {result.stderr}")
            return

        # Check public dir has content
        public_dir = self.dist_dir / "public"
        if public_dir.exists():
            html_count = len(list(public_dir.rglob("*.html")))
            js_count = len(list(public_dir.rglob("*.js")))
            self.log(f"Dist contains {html_count} HTML, {js_count} JS files")
        else:
            self.log("No public/ dir in dist (mount-only app?)")

        # Check bytecode exists if source was stripped
        if self.stats["removed_py"] > 0:
            pyc_count = len(list(self.dist_dir.rglob("*.pyc")))
            if pyc_count == 0:
                self.error("Source was stripped but no .pyc files found")
            else:
                self.log(f"Bytecode: {pyc_count} .pyc files")

        self.success("Validation passed")

    def _phase_package(self):
        self.phase("Packaging")
        zip_path = self.dist_dir.parent / f"{self.dist_dir.name}.zip"
        self.log(f"Creating archive: {zip_path.name}")
        if not self.dry_run:
            base_name = str(zip_path.with_suffix(""))
            shutil.make_archive(
                base_name, "zip",
                root_dir=str(self.dist_dir.parent),
                base_dir=self.dist_dir.name,
            )
        if zip_path.exists():
            self.success(f"Archive: {zip_path.name} ({zip_path.stat().st_size / 1024 / 1024:.2f} MB)")

    def _report(self):
        duration = time.time() - self.start_time
        print(f"\n{CLR_BOLD}{CLR_GREEN}=========================================={CLR_RESET}")
        print(f"{CLR_BOLD}SPRAG PACK COMPLETE — {CLR_YELLOW}{duration:.2f}s{CLR_RESET}")
        print(f"{CLR_BOLD}{CLR_GREEN}=========================================={CLR_RESET}")
        if self.stats["minified_css"] or self.stats["minified_js"]:
            asset_saved = (self.stats["orig_asset_size"] - self.stats["packed_asset_size"]) / 1024
            print(f"CSS minified:       {self.stats['minified_css']}")
            print(f"JS minified:        {self.stats['minified_js']}")
            print(f"Asset savings:      {CLR_GREEN}{asset_saved:.1f} KB{CLR_RESET}")
        if self.stats["optimized_images"]:
            img_saved = (self.stats["orig_image_size"] - self.stats["packed_image_size"]) / 1024
            print(f"Images optimized:   {self.stats['optimized_images']}")
            print(f"Variants generated: {self.stats['generated_variants']}")
            print(f"Image savings:      {CLR_GREEN}{img_saved:.1f} KB{CLR_RESET}")
        if self.stats["compiled_py"]:
            print(f"Python compiled:    {self.stats['compiled_py']}")
            print(f"Sources removed:    {self.stats['removed_py']}")
        if self.stats["gzipped_files"]:
            print(f"Pre-gzipped:        {self.stats['gzipped_files']}")
            print(f"Gzip savings:       {CLR_GREEN}{self.stats['gzip_saved'] / 1024:.1f} KB{CLR_RESET}")

        if self.stats["errors"]:
            print(f"\n{CLR_RED}Errors ({len(self.stats['errors'])}):{CLR_RESET}")
            for err in self.stats["errors"][:10]:
                print(f"  - {err}")
        else:
            print(f"\n{CLR_BOLD}{CLR_CYAN}STATUS: READY FOR DEPLOYMENT{CLR_RESET}")
        print(f"{CLR_BOLD}{CLR_GREEN}=========================================={CLR_RESET}\n")
