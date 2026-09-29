/**
 * electron-builder `beforePack` hook: refuse to package without a frozen
 * engine built for the target platform, so an installer can never ship
 * without its processing backend.
 */
const { existsSync, readFileSync } = require('node:fs')
const { join } = require('node:path')

const ARCH_NAMES = { 0: 'ia32', 1: 'x64', 2: 'armv7l', 3: 'arm64', 4: 'universal' }

exports.default = async function verifyEngine(context) {
  const platform = context.electronPlatformName // 'win32' | 'darwin' | 'linux'
  const arch = ARCH_NAMES[context.arch] ?? String(context.arch)
  const dir = join(context.packager.projectDir, 'resources', 'engine')
  const exe = join(dir, platform === 'win32' ? 'livestock-engine.exe' : 'livestock-engine')
  if (!existsSync(exe)) {
    throw new Error(
      `Frozen engine not found at ${exe}.\n` +
        `Build it on this ${platform}/${arch} machine first: python packaging/build_engine.py`
    )
  }
  if (platform !== process.platform) {
    throw new Error(
      `Cannot package for ${platform} on ${process.platform}: PyInstaller engines are not cross-platform. ` +
        'Build each installer on its own OS (see .github/workflows/release.yml).'
    )
  }
  const infoPath = join(dir, 'engine-info.json')
  if (existsSync(infoPath)) {
    const info = JSON.parse(readFileSync(infoPath, 'utf8'))
    console.log(`  • bundling frozen engine ${info.version} (${info.bundle_mb} MB, self-check ok=${info.ok})`)
  }
}
