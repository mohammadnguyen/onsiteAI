// Metro already applies this preset; the file exists so Jest's babel-jest
// transform resolves the same one. Keep it in step with the SDK.
//
// `unstable_transformImportMeta` rewrites `import.meta` for the classic
// script output the web bundle uses. Without it the web bundle throws
// "Cannot use 'import.meta' outside a module" before the app mounts - which
// it does on this checkout with no babel config at all, so this is not a
// behaviour this file introduced.
module.exports = function (api) {
  api.cache(true);
  return {
    presets: [['babel-preset-expo', { unstable_transformImportMeta: true }]],
  };
};
