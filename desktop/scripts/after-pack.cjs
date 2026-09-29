/**
 * electron-builder `afterPack` hook: drop files an offline production app
 * must not carry (Electron's default app, auto-update metadata).
 */
const { rmSync } = require('node:fs')
const { join } = require('node:path')

exports.default = async function afterPack(context) {
  const resources =
    context.electronPlatformName === 'darwin'
      ? join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, 'Contents', 'Resources')
      : join(context.appOutDir, 'resources')
  for (const name of ['default_app.asar', 'app-update.yml']) {
    rmSync(join(resources, name), { force: true })
  }
}
