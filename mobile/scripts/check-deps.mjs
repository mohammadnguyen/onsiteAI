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
 * Every installed path that carries expo-module.config.json, grouped by
 * package name. The lockfile gives the paths and versions; the file on disk
 * decides which of them autolinking will actually try to build.
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
    if (!existsSync(join(projectRoot, path, 'expo-module.config.json'))) continue
    if (!byName.has(name)) byName.set(name, [])
    byName.get(name).push({ path, version: entry.version ?? '?' })
  }
  return byName
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
}

function runExpoDoctor() {
  process.stdout.write('\nexpo-doctor (second opinion):\n')
  const result = spawnSync('expo-doctor', [], {
    cwd: projectRoot,
    encoding: 'utf8',
    shell: process.platform === 'win32',
  })

  if (result.error) {
    fail([
      '',
      'expo-doctor could not be started, so the second opinion is missing.',
      'It is a pinned devDependency - run `npm ci` and try again.',
      `  ${result.error.message}`,
    ])
  }

  const output = `${result.stdout ?? ''}${result.stderr ?? ''}`
  process.stdout.write(output.endsWith('\n') ? output : `${output}\n`)

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

  process.stdout.write(
    '\nexpo-doctor\'s own exit code is advisory here - see the note at the top\n' +
      'of scripts/check-deps.mjs. Its remaining output is for a human to read.\n',
  )
}

checkSingleCopies()
runExpoDoctor()
process.stdout.write('\ndependency gate PASSED\n')
