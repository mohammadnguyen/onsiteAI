#!/usr/bin/env node
/**
 * The dependency gate. Run by `npm run doctor`.
 *
 * WHY THIS EXISTS. A lockfile that pinned expo-asset 57.0.18 into an SDK 54
 * app reached a real iPhone and opened to a white screen. Two majors of two
 * native modules were installed at once; autolinking compiles one, the JS
 * resolves the other, and the bundle dies while it is being evaluated -
 * before React mounts, so no error boundary exists to catch it. `tsc` and
 * Jest cannot see any of that: neither resolves a native module.
 *
 * WHAT IT CHECKS, and why in this order:
 *
 *  1. OUR OWN INVARIANT, and the one that fails the build. Every autolinked
 *     native module must be installed exactly once. "Autolinked" is not a
 *     guess from the package name - it is the presence of
 *     expo-module.config.json, which is the same file Expo's autolinking
 *     reads. Pure-JS build tooling may legitimately appear at several paths
 *     and is ignored, so this needs no allow-list to maintain.
 *
 *  1b. EVERY NATIVE PEER DECLARED DIRECTLY, also ours and also fatal.
 *     Autolinking links what the app itself depends on; a native peer left
 *     to resolve transitively is fetched as the registry's latest, which
 *     is precisely how the 57.0.18 copy arrived. This rule is owned here
 *     rather than delegated, because expo-doctor's equivalent check
 *     reports SUCCESS when it cannot load module metadata - a degraded run
 *     and a clean one are indistinguishable from its output.
 *
 *  2. expo-doctor, as a SECOND OPINION. Its own exit code is deliberately
 *     not the gate: under npm 11 two of its checks abort on `npm explain`
 *     returning non-zero for a package that is simply absent, so it can
 *     never exit 0 here through no fault of this project. Instead the two
 *     checks that would have caught the original defect are read by name
 *     and DO fail the gate:
 *         "Check that required peer dependencies are installed"
 *         "Check that no duplicate dependencies are installed"
 *     Everything else it reports is printed for a human and does not fail.
 *
 *     REQUIRED TO PASS, NOT MERELY NOT-TO-FAIL. An earlier version looked
 *     for the failure marker and treated its absence as success, so an
 *     expo-doctor that aborted before running anything - an invalid
 *     FOREY_VARIANT makes it die during config evaluation - printed
 *     "PASSED". It is run with --verbose, which prints a tick per passing
 *     check, and each mandatory check must be positively ticked. Silence
 *     is a failure.
 *
 * Offline and reproducible: it reads package-lock.json and the installed
 * tree. It downloads nothing - expo-doctor is a pinned devDependency, so
 * `npm ci && npm run doctor` behaves identically on any machine.
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/** Checks whose failure is a real defect, whatever else expo-doctor says. */
const FATAL_DOCTOR_CHECKS = [
  'Check that required peer dependencies are installed',
  'Check that no duplicate dependencies are installed',
]

function fail(lines) {
  for (const l of lines) process.stdout.write(`${l}\n`)
  process.exit(1)
}

/**
 * Is this installed package one that autolinking will try to BUILD?
 *
 * Two families, because this project has both:
 *  - Expo modules, marked by expo-module.config.json, the file Expo's
 *    autolinking reads.
 *  - React Native community modules, which have no such file. They are
 *    recognised the way the RN CLI does in practice: a package that ships
 *    BOTH an android/ and an ios/ directory has native source to compile.
 *    react-native-screens and react-native-safe-area-context are both of
 *    these, both are native peers of expo-router, and an inventory built
 *    only from Expo manifests silently skipped them.
 *
 * Requiring both directories rather than either keeps out packages that
 * merely ship an example app for one platform.
 */
function isNativePackage(installedPath) {
  const dir = join(projectRoot, installedPath)
  if (existsSync(join(dir, 'expo-module.config.json'))) return true
  return existsSync(join(dir, 'android')) && existsSync(join(dir, 'ios'))
}

/**
 * Every installed path holding a native module, grouped by package name.
 * The lockfile gives the paths and versions; the files on disk decide
 * which of them autolinking will actually try to build.
 */
function nativeModulesByName() {
  const lockPath = join(projectRoot, 'package-lock.json')
  if (!existsSync(lockPath)) {
    fail(['package-lock.json not found. Run `npm install` first.'])
  }
  const lock = JSON.parse(readFileSync(lockPath, 'utf8'))
  const byName = new Map()

  for (const [path, entry] of Object.entries(lock.packages ?? {})) {
    if (path === '' || !path.startsWith('node_modules/')) continue
    if (entry.link) continue
    const name = path.slice(path.lastIndexOf('node_modules/') + 'node_modules/'.length)
    if (!isNativePackage(path)) continue
    if (!byName.has(name)) byName.set(name, [])
    byName.get(name).push({ path, version: entry.version ?? '?' })
  }
  return byName
}

/**
 * Every native module another native module declares as a peer must be a
 * DIRECT dependency of this app.
 *
 * Autolinking links what the app itself depends on. A native peer left to
 * be satisfied transitively is resolved by npm against the registry's
 * latest, which is exactly how expo-asset 57.0.18 arrived in an SDK 54
 * app. expo-doctor has a check for this, but its tick cannot be trusted as
 * proof: on pinned 1.20.4 the check reports success when it could not load
 * module metadata, so a degraded run and a clean one look identical from
 * outside. This owns the rule instead of trusting that.
 */
function checkNativePeersAreDirect(native, appDeps) {
  const problems = []

  for (const [name, paths] of native) {
    const pkgPath = join(projectRoot, paths[0].path, 'package.json')
    if (!existsSync(pkgPath)) continue
    let peers
    try {
      peers = JSON.parse(readFileSync(pkgPath, 'utf8')).peerDependencies ?? {}
    } catch {
      problems.push(`  ${name}: its package.json could not be read`)
      continue
    }
    for (const [peer, range] of Object.entries(peers)) {
      // Only native peers matter: a pure-JS peer is resolved by bundling,
      // not by autolinking, so a transitive copy of one is harmless.
      if (!native.has(peer)) continue
      if (!Object.prototype.hasOwnProperty.call(appDeps, peer)) {
        problems.push(
          `  ${peer} is a native peer of ${name} (${range}) but is not a ` +
            'direct dependency of this app',
        )
      }
    }
  }

  if (problems.length > 0) {
    fail([
      '',
      'NATIVE PEER DEPENDENCIES NOT DECLARED DIRECTLY.',
      '',
      'Autolinking links what this app depends on. A native peer satisfied',
      'only transitively is resolved against the registry\'s latest, which',
      'is how an SDK-incompatible version arrives without anyone asking.',
      '',
      ...problems,
      '',
      'Fix with `npx expo install <name>`, which picks the SDK-compatible',
      'version and records it in package.json.',
    ])
  }

  process.stdout.write('every native peer dependency declared directly - OK\n')
}

function checkSingleCopies() {
  const byName = nativeModulesByName()
  if (byName.size === 0) {
    fail([
      'No autolinked native modules found on disk.',
      'node_modules is missing or incomplete - run `npm ci` before this gate.',
    ])
  }

  const duplicates = [...byName.entries()].filter(([, paths]) => paths.length > 1)
  if (duplicates.length > 0) {
    const lines = [
      '',
      'DUPLICATE NATIVE MODULES - this build would be broken on a device.',
      '',
      'Autolinking compiles one native version while the JS resolves another.',
      'The usual cause is `npm install <pkg>` resolving `latest` instead of the',
      'version this SDK pins. Fix with `npx expo install <pkg>`, which picks the',
      'SDK-compatible version, then re-run this gate.',
      '',
    ]
    for (const [name, paths] of duplicates) {
      lines.push(`  ${name}`)
      for (const p of paths) lines.push(`      ${p.version.padEnd(12)} ${p.path}`)
    }
    lines.push('')
    fail(lines)
  }

  process.stdout.write(
    `one copy of each of ${byName.size} autolinked native modules - OK\n`,
  )
  return byName
}

function runExpoDoctor() {
  process.stdout.write('\nexpo-doctor (second opinion):\n')
  // --verbose so passing checks are printed too. Without it a check that
  // never ran is indistinguishable from one that passed.
  const result = spawnSync('expo-doctor', ['--verbose'], {
    cwd: projectRoot,
    encoding: 'utf8',
    shell: process.platform === 'win32',
    // Colour off. With FORCE_COLOR set, expo-doctor colours the tick
    // separately from the text and leaves an ANSI reset between them, so
    // matching "✔ <title>" fails on output that actually passed - the gate
    // would then reject a perfectly good tree.
    env: { ...process.env, NO_COLOR: '1', FORCE_COLOR: '0' },
  })

  if (result.error) {
    fail([
      '',
      'expo-doctor could not be started, so the second opinion is missing.',
      'It is a pinned devDependency - run `npm ci` and try again.',
      `  ${result.error.message}`,
    ])
  }

  const raw = `${result.stdout ?? ''}${result.stderr ?? ''}`
  process.stdout.write(raw.endsWith('\n') ? raw : `${raw}\n`)
  // Belt and braces with NO_COLOR above: strip any escape sequence that
  // survives, so the matching below sees plain text whatever the terminal
  // or CI has set.
  // eslint-disable-next-line no-control-regex
  const output = raw.replace(/\u001B\[[0-9;]*m/g, '')

  const failedFatal = FATAL_DOCTOR_CHECKS.filter((check) =>
    output.includes(`✖ ${check}`),
  )
  if (failedFatal.length > 0) {
    fail([
      '',
      'expo-doctor reported a check that this gate treats as fatal:',
      ...failedFatal.map((c) => `  ✖ ${c}`),
      '',
      'These two are the checks that would have caught the expo-asset defect.',
    ])
  }

  // Positive confirmation. A check that produced no verdict at all - because
  // expo-doctor aborted, or because a future version renamed it - must not
  // read as a pass.
  const missing = FATAL_DOCTOR_CHECKS.filter(
    (check) => !output.includes(`✔ ${check}`),
  )
  if (missing.length > 0) {
    fail([
      '',
      'expo-doctor did not report a result for a check this gate requires:',
      ...missing.map((c) => `  ? ${c}`),
      '',
      'It either aborted before running, or the check has been renamed.',
      'Read its output above. This gate does not treat silence as success.',
    ])
  }

  process.stdout.write(
    '\nexpo-doctor\'s own exit code is advisory here - see the note at the top\n' +
      'of scripts/check-deps.mjs. Its remaining output is for a human to read.\n',
  )
}

const appPkg = JSON.parse(readFileSync(join(projectRoot, 'package.json'), 'utf8'))
const native = checkSingleCopies()
checkNativePeersAreDirect(native, appPkg.dependencies ?? {})
runExpoDoctor()
process.stdout.write('\ndependency gate PASSED\n')
