import { ragotRegistry } from '../vendor/ragot.esm.min.js';

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
