import { ragotRegistry } from '../vendor/ragot.esm.min.js';

function stopOwnedChild(child) {
    if (!child) {
        return;
    }
    if (typeof child.stop === 'function') {
        child.stop();
        return;
    }
    if (typeof child.close === 'function') {
        child.close();
        return;
    }
    if (typeof child.unmount === 'function') {
        child.unmount();
    }
}

export class RuntimeOwner {
    constructor(name = 'sprag.runtime.owner') {
        this.name = name;
        this._cleanups = [];
        this._stopped = false;
        this.stopReason = null;
    }

    addCleanup(fn) {
        if (typeof fn !== 'function') {
            return fn;
        }
        if (this._stopped) {
            try {
                fn();
            } catch (error) {
                console.warn('[SPRAG] Late cleanup failed.', error);
            }
            return fn;
        }
        this._cleanups.push(fn);
        return fn;
    }

    add_cleanup(fn) {
        return this.addCleanup(fn);
    }

    adopt(child) {
        if (!child) {
            return child;
        }
        this.addCleanup(() => stopOwnedChild(child));
        return child;
    }

    stop(reason = 'stop') {
        if (this._stopped) {
            return;
        }
        this._stopped = true;
        this.stopReason = reason;
        const cleanups = this._cleanups.slice().reverse();
        this._cleanups.length = 0;
        for (const cleanup of cleanups) {
            try {
                cleanup();
            } catch (error) {
                console.warn('[SPRAG] Runtime cleanup failed.', error);
            }
        }
    }

    get stopped() {
        return this._stopped;
    }
}

export function providerRegistryKeys(key, instance) {
    const keys = [key];
    const legacyName = instance && typeof instance.name === 'string'
        ? instance.name.trim()
        : '';
    if (legacyName && !keys.includes(legacyName)) {
        keys.push(legacyName);
    }
    return keys;
}

export function provideOwned(key, value, owner) {
    ragotRegistry.provide(key, value, owner, { replace: true });
    return key;
}
