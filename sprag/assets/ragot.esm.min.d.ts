export type AnyRecord = Record<string, unknown>;
export type CleanupFn = () => void;
export type UnsubscribeFn = () => void;
export type SelectorFn<S, R> = (state: S) => R;
export type StatePatch<S extends AnyRecord> = Partial<S> | ((state: S, store: StateStore<S>) => Partial<S>);
export type ChildLifecycle = Record<string, unknown>;
export interface LifecycleOwner {
    addCleanup(cleanup: CleanupFn): unknown;
}

export interface EventBus {
    on<T = unknown>(event: string, callback: (data: T) => void): UnsubscribeFn;
    off<T = unknown>(event: string, callback: (data: T) => void): void;
    emit<T = unknown>(event: string, data?: T): void;
    once<T = unknown>(event: string, callback: (data: T) => void): UnsubscribeFn;
    clear(event?: string): void;
}

export interface AdoptOptions {
    startMethod?: string;
    stopMethod?: string;
    startArgs?: unknown[];
}

export interface SubscribeOptions<S extends AnyRecord, R = S> {
    selector?: SelectorFn<S, R>;
    immediate?: boolean;
    owner?: LifecycleOwner;
}

export interface WatchStateOptions<S extends AnyRecord, R = S> {
    selector?: SelectorFn<S, R>;
    immediate?: boolean;
}

export interface AdoptComponentOptions<S extends AnyRecord, C extends Component<AnyRecord> = Component<AnyRecord>> extends AdoptOptions {
    startMethod?: string;
    stopMethod?: string;
    startArgs?: unknown[];
    sync?: ((component: C, state: S, module: Module<S>) => void) | null;
}

export interface SocketLike {
    on(event: string, handler: (...args: unknown[]) => void): unknown;
    off(event: string, handler: (...args: unknown[]) => void): unknown;
}

export class Module<S extends AnyRecord = AnyRecord> {
    state: S;
    constructor(initialState?: S);
    onStart(): void;
    onStop(): void;
    setState(newState: Partial<S>): void;
    batchState(mutatorFn: (state: S) => void): this;
    subscribe(fn: (state: S, module: this) => void, options?: Omit<SubscribeOptions<S, S>, 'selector'> & { selector?: undefined }): UnsubscribeFn;
    subscribe<Sel extends (state: S) => any>(fn: (slice: ReturnType<Sel>, state: S, module: this) => void, options: { selector: Sel; immediate?: boolean; owner?: LifecycleOwner }): UnsubscribeFn;
    watchState(fn: (state: S, module: this) => void, options?: Omit<WatchStateOptions<S, S>, 'selector'> & { selector?: undefined }): UnsubscribeFn;
    watchState<Sel extends (state: S) => any>(fn: (slice: ReturnType<Sel>, state: S, module: this) => void, options: { selector: Sel; immediate?: boolean }): UnsubscribeFn;
    start(): this;
    stop(): this;
    on(target: EventTarget | null | undefined, type: string, handler: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions): this;
    off(target: EventTarget | null | undefined, type: string, handler: EventListenerOrEventListenerObject): this;
    listen<T = unknown>(event: string, handler: (data: T) => void): this;
    emit<T = unknown>(event: string, data?: T): this;
    timeout(callback: () => void, delayMs: number): ReturnType<typeof setTimeout>;
    interval(callback: () => void, delayMs: number): ReturnType<typeof setInterval>;
    clearTimeout(timeoutId: ReturnType<typeof setTimeout>): this;
    clearInterval(intervalId: ReturnType<typeof setInterval>): this;
    clearTimers(): this;
    addCleanup(cleanup: CleanupFn): this;
    delegate(parent: Element | string, event: string, selector: string, handler: (event: Event, target: Element) => void): this;
    adopt(child: ChildLifecycle | null | undefined, options?: AdoptOptions): this;
    createSelector<R>(inputSelectors: Array<SelectorFn<S, unknown>>, resultFunc: (...values: unknown[]) => R): SelectorFn<S, R>;
    onSocket(socket: SocketLike | null | undefined, event: string, handler: (...args: unknown[]) => void): this;
    offSocket(socket: SocketLike | null | undefined, event: string, handler: (...args: unknown[]) => void): this;
    adoptComponent<C extends Component<AnyRecord>>(component: C, options?: AdoptComponentOptions<S, C>): this;
}

export class Component<S extends AnyRecord = AnyRecord> {
    state: S;
    element: HTMLElement | null;
    refs: Record<string, Element>;
    constructor(initialState?: S);
    onStart(): void;
    onStop(): void;
    render(): HTMLElement;
    setState(newState: Partial<S>): void;
    setStateSync(newState: Partial<S>): void;
    ref(name: string): (el: Element) => void;
    mount(parentDiv: HTMLElement | null | undefined): HTMLElement | null;
    mountBefore(sibling: Node | null | undefined): HTMLElement | null;
    unmount(): void;
    on(target: EventTarget | null | undefined, type: string, handler: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions): this;
    off(target: EventTarget | null | undefined, type: string, handler: EventListenerOrEventListenerObject): this;
    listen<T = unknown>(event: string, handler: (data: T) => void): this;
    emit<T = unknown>(event: string, data?: T): this;
    timeout(callback: () => void, delayMs: number): ReturnType<typeof setTimeout>;
    interval(callback: () => void, delayMs: number): ReturnType<typeof setInterval>;
    clearTimeout(timeoutId: ReturnType<typeof setTimeout>): this;
    clearInterval(intervalId: ReturnType<typeof setInterval>): this;
    clearTimers(): this;
    addCleanup(cleanup: CleanupFn): this;
    delegate(parent: Element | string, event: string, selector: string, handler: (event: Event, target: Element) => void): this;
    adopt(child: ChildLifecycle | null | undefined, options?: AdoptOptions): this;
    createSelector<R>(inputSelectors: Array<SelectorFn<S, unknown>>, resultFunc: (...values: unknown[]) => R): SelectorFn<S, R>;
}

export interface WaitForOptions {
    timeoutMs?: number;
}

export interface ProvideOptions {
    replace?: boolean;
}

export interface CancellableWait<T = unknown> {
    promise: Promise<T>;
    cancel(): void;
}

export interface RAGOTRegistry {
    provide<T>(key: string, value: T, owner?: LifecycleOwner | null, options?: ProvideOptions): T;
    unregister(key: string, token?: symbol | null): boolean;
    resolve<T = unknown>(key: string): T | undefined;
    require<T = unknown>(key: string): T;
    has(key: string): boolean;
    list(): string[];
    clear(): void;
    waitFor<T = unknown>(key: string, options?: WaitForOptions): Promise<T>;
    waitForCancellable<T = unknown>(key: string, options?: WaitForOptions): CancellableWait<T>;
}

export type RAGOTModules = Readonly<Record<string, unknown>>;

export interface StoreSubscribeOptions<S, R = S> {
    selector?: (state: S) => R;
    equals?: (a: R, b: R) => boolean;
    immediate?: boolean;
}

export interface ChangeMeta {
    type: string;
    path?: string[];
    value?: unknown;
    prevValue?: unknown;
    version: number;
    store: string;
    timestamp: number;
    meta?: Record<string, unknown>;
    changes?: ChangeMeta[];
}

export interface StateStore<S extends AnyRecord = AnyRecord> {
    name: string;
    actions: Record<string, (...args: unknown[]) => unknown>;
    getState(): S;
    get<T = unknown>(path: string | string[], fallbackValue?: T): T;
    set<T = unknown>(path: string | string[], value: T, meta?: Record<string, unknown>): T;
    setState(partial: StatePatch<S>, meta?: Record<string, unknown>): S;
    patch(partial: StatePatch<S>, meta?: Record<string, unknown>): S;
    batch(mutator: (state: S, store: StateStore<S>) => void, meta?: Record<string, unknown>): S;
    compareAndSet<T = unknown>(path: string | string[], expectedValue: T, nextValue: T, meta?: Record<string, unknown>): boolean;
    subscribe(listener: (state: S, change: ChangeMeta, store: StateStore<S>) => void, options?: Omit<StoreSubscribeOptions<S, S>, 'selector'> & { selector?: undefined }): UnsubscribeFn;
    subscribe<Sel extends (state: S) => any>(listener: (slice: ReturnType<Sel>, change: ChangeMeta, store: StateStore<S>, prevSlice?: ReturnType<Sel>) => void, options: { selector: Sel; equals?: (a: ReturnType<Sel>, b: ReturnType<Sel>) => boolean; immediate?: boolean }): UnsubscribeFn;
    registerActions<T extends Record<string, (store: StateStore<S>, ...args: never[]) => unknown>>(definitions: T | ((store: StateStore<S>) => T)): Record<keyof T, (...args: Parameters<T[keyof T]> extends [StateStore<S>, ...infer A] ? A : never[]) => ReturnType<T[keyof T]>>;
    dispatch<T = unknown>(actionName: string, ...args: unknown[]): T;
    listActions(): string[];
    getVersion(): number;
    getLastChange(): ChangeMeta | null;
    createSelector<R>(inputSelectors: Array<(state: S) => unknown>, resultFunc: (...values: unknown[]) => R): (state: S) => R;
}

export interface CreateStateStoreOptions {
    name?: string;
}

export type ElementChild = Node | string | number | boolean | null | undefined | ElementChild[];
export type ElementEvents = Record<string, EventListenerOrEventListenerObject>;

export interface CreateElementOptions {
    className?: string | string[];
    class?: string | string[];
    style?: Partial<CSSStyleDeclaration> | Record<string, string | number>;
    dataset?: Record<string, string | number | boolean | null | undefined>;
    id?: string;
    ref?: (el: Element) => void;
    events?: ElementEvents;
    textContent?: string | number;
    innerHTML?: string;
    children?: ElementChild | ElementChild[];
    disabled?: boolean;
    [attribute: string]: unknown;
}

export interface RenderListOptions {
    poolKey?: string | null;
}

export interface RenderGridOptions extends RenderListOptions {
    columns?: number;
    columnWidth?: string;
    gap?: string;
    applyGridStyles?: boolean;
}

export interface InfiniteScrollController {
    reset(): void;
    destroy(): void;
}

export interface InfiniteScrollOptions {
    sentinel: HTMLElement;
    topSentinel: HTMLElement;
    onLoadMore(chunkIndex: number): void | Promise<unknown>;
    onEvictChunk(chunkIndex: number): void;
    onLoadDirection?(ctx: { direction: 'forward' | 'backward'; batchCount: number; firstTarget: number; step: number }): void | Promise<unknown>;
    seekToViewport?(direction: 'forward' | 'backward'): boolean;
    visibleChunks(): Set<number>;
    totalItems(): number;
    getChunkEl?(chunkIndex: number): HTMLElement | null;
    chunkSize?: number;
    maxChunks?: number;
    rootMargin?: string;
    root?: HTMLElement | null;
    axis?: 'auto' | 'horizontal' | 'vertical';
}

export interface LazyRetryContext {
    attempt: number;
    currentSrc: string;
}

export interface LazyRetryOptions {
    maxAttempts?: number;
    baseDelayMs?: number;
    backoffFactor?: number;
    shouldRetry?(img: HTMLImageElement, ctx: LazyRetryContext): boolean;
    getNextSrc?(img: HTMLImageElement, attempt: number, currentSrc: string, ctx: LazyRetryContext): string | null | undefined;
    schedule?(fn: () => void, delayMs: number): unknown;
    onRetry?(img: HTMLImageElement, ctx: LazyRetryContext): void;
}

export interface LazyLoaderOptions {
    selector?: string;
    root?: HTMLElement | null;
    rootMargin?: string;
    concurrency?: number;
    retry?: boolean | LazyRetryOptions | null;
    onStateChange?(img: HTMLImageElement, state: 'pending' | 'loaded' | 'error', ctx?: Record<string, unknown>): void;
    onLoad?(img: HTMLImageElement): void;
    onError?(img: HTMLImageElement, ctx: LazyRetryContext): void;
}

export interface LazyLoaderController {
    observe(img: HTMLImageElement): void;
    reset(img: HTMLImageElement): void;
    prime(img: HTMLImageElement, options?: { fetchPriority?: string }): void;
    refresh(): void;
    destroy(): void;
}

export interface VirtualScrollerLoadContext {
    token: number;
    isCurrent(): boolean;
}

export interface VirtualScrollerDebugHooks {
    includeSnapshot?: boolean;
    onEvent?(event: Record<string, unknown>): void;
}

export interface VirtualScrollerOptions {
    renderChunk(chunkIndex: number, ctx?: VirtualScrollerLoadContext): HTMLElement | null | undefined | Promise<HTMLElement | null | undefined>;
    totalItems(): number;
    chunkSize: number;
    measureChunk?(el: HTMLElement, chunkIndex: number): number;
    buildPlaceholder?(chunkIndex: number, px: number): HTMLElement;
    maxChunks?: number;
    root?: HTMLElement | null;
    rootMargin?: string;
    containerClass?: string;
    initialChunks?: number;
    chunkContainer?: HTMLElement;
    onChunkEvicted?(chunkIndex: number): void;
    childPoolSize?: number;
    poolSize?: number;
    onRecycle?(el: HTMLElement, chunkIndex: number): void;
    axis?: 'auto' | 'horizontal' | 'vertical';
    debugLabel?: string;
    debugHooks?: VirtualScrollerDebugHooks;
}

export interface VirtualScrollerDebugState {
    label: string;
    axis: string;
    visibleChunks: number[];
    rootConnected: boolean;
    rootScrollTop: number | null;
    rootScrollLeft: number | null;
    clientHeight: number | null;
    clientWidth: number | null;
    scrollHeight: number | null;
    scrollWidth: number | null;
    topSentinelConnected: boolean;
    bottomSentinelConnected: boolean;
    [key: string]: unknown;
}

export class VirtualScroller extends Component<AnyRecord> {
    constructor(options?: VirtualScrollerOptions);
    reset(): void;
    jumpToIndex(targetIndex: number): boolean;
    getVisibleChunks(): Set<number>;
    getChunkElement(i: number): Element | null;
    seekToViewport(direction?: 'forward' | 'backward'): boolean;
    refreshChunkMeasurement(i: number): number;
    getDebugState(): VirtualScrollerDebugState;
    acquireChild(chunkIndex: number, options: VirtualScrollerOptions, parentEl: HTMLElement): VirtualScroller;
    recycle(): void;
    rebind(options: Partial<VirtualScrollerOptions>, parentEl: HTMLElement): void;
}

export interface RAGOTNamespace {
    $: typeof $;
    $$: typeof $$;
    bus: EventBus;
    ragotRegistry: RAGOTRegistry;
    ragotModules: RAGOTModules;
    createStateStore: typeof createStateStore;
    createSelector: typeof createSelector;
    createElement: typeof createElement;
    batchAppend: typeof batchAppend;
    append: typeof append;
    prepend: typeof prepend;
    insertBefore: typeof insertBefore;
    remove: typeof remove;
    morphDOM: typeof morphDOM;
    Module: typeof Module;
    Component: typeof Component;
    clear: typeof clear;
    delegateEvent: typeof delegateEvent;
    css: typeof css;
    attr: typeof attr;
    createIcon: typeof createIcon;
    show: typeof show;
    hide: typeof hide;
    toggle: typeof toggle;
    renderList: typeof renderList;
    renderGrid: typeof renderGrid;
    clearPool: typeof clearPool;
    createInfiniteScroll: typeof createInfiniteScroll;
    VirtualScroller: typeof VirtualScroller;
    createLazyLoader: typeof createLazyLoader;
    animateIn: typeof animateIn;
    animateOut: typeof animateOut;
    createApp: typeof createApp;
}

export function $<E extends Element = Element>(selector: string, parent?: ParentNode): E | null;
export function $$<E extends Element = Element>(selector: string, parent?: ParentNode): E[];
export const bus: EventBus;
export const ragotRegistry: RAGOTRegistry;
export const ragotModules: RAGOTModules;
export function createSelector<S = unknown, R = unknown>(inputSelectors: Array<(state: S) => unknown>, resultFunc: (...values: unknown[]) => R): (state: S) => R;
export function createStateStore<S extends AnyRecord = AnyRecord>(initialState?: S, options?: CreateStateStoreOptions): StateStore<S>;
export function createElement<K extends keyof HTMLElementTagNameMap>(tag: K, options?: CreateElementOptions | null, ...children: ElementChild[]): HTMLElementTagNameMap[K];
export function createElement<K extends keyof SVGElementTagNameMap>(tag: K | `svg:${K}`, options?: CreateElementOptions | null, ...children: ElementChild[]): SVGElementTagNameMap[K];
export function createElement(tag: string, options?: CreateElementOptions | null, ...children: ElementChild[]): Element;
export function batchAppend(parent: Element, children: Node | Node[]): Promise<void>;
export function append<T extends HTMLElement>(parent: T, ...children: ElementChild[]): T;
export function prepend<T extends HTMLElement>(parent: T, ...children: ElementChild[]): T;
export function insertBefore<T extends Node>(parent: HTMLElement, newNode: T, referenceNode: Node | null): T;
export function remove<T extends Element>(el: T): T;
export function morphDOM<T extends Node>(oldNode: T | null | undefined, newNode: T | null | undefined): T | null | undefined;
export function renderList<T>(container: HTMLElement, items: T[], getKey: (item: T) => string | number, renderItem: (item: T) => HTMLElement, updateItem?: (el: HTMLElement, item: T) => void, options?: RenderListOptions): void;
export function renderGrid<T>(container: HTMLElement, items: T[], getKey: (item: T) => string | number, renderItem: (item: T) => HTMLElement, updateItem?: (el: HTMLElement, item: T) => void, options?: RenderGridOptions): void;
export function clearPool(poolKey?: string): void;
export function createInfiniteScroll(owner: LifecycleOwner, options: InfiniteScrollOptions): InfiniteScrollController;
export function createLazyLoader(owner: LifecycleOwner, options?: LazyLoaderOptions): LazyLoaderController;
export function clear(el: Element | null | undefined): void;
export function delegateEvent(parent: Element | string, event: string, selector: string, handler: (this: Element, event: Event, target: Element) => void): UnsubscribeFn;
export function css<T extends HTMLElement>(el: T, styles: Partial<CSSStyleDeclaration> | Record<string, string | number>): T;
export function attr<T extends HTMLElement>(el: T, attributes: Record<string, unknown>, options?: { additive?: boolean }): T;
export function createIcon(svgString: string, className?: string): HTMLSpanElement;
export function show<T extends HTMLElement>(el: T): T;
export function hide<T extends HTMLElement>(el: T): T;
export function toggle<T extends HTMLElement>(el: T, force?: boolean): T;
export function animateIn(el: HTMLElement | null | undefined, activeClass?: string): void;
export function animateOut(el: HTMLElement | null | undefined, activeClass?: string, remove?: boolean): Promise<void>;
export function createApp<S extends AnyRecord, C extends Component<S>>(ComponentClass: new (initialState?: S) => C, container: string | HTMLElement, initialState?: S, globalName?: string): C | null;

declare const RAGOT: RAGOTNamespace;
export default RAGOT;
