/**
 * Registry of files the user has admitted (dropped or picked in a dialog).
 *
 * The renderer only ever refers to files by the opaque ids issued here, so a
 * compromised renderer cannot point the engine at arbitrary paths: every path
 * is checked once on admission (absolute, regular file, allowed extension,
 * size limit) and the engine is only given paths from this registry.
 */

import { randomBytes } from 'node:crypto'
import { open, stat } from 'node:fs/promises'
import { basename, isAbsolute, resolve } from 'node:path'

import { ACCEPTED_EXTENSIONS, classifyFile, extensionOf } from '@shared/fileKinds'
import type { IngestedFile, InspectResult } from '@shared/types'

export const MAX_FILE_BYTES = 4 * 1024 ** 3
const HEAD_BYTES = 16 * 1024

async function readHead(path: string): Promise<string> {
  const handle = await open(path, 'r')
  try {
    const buffer = Buffer.alloc(HEAD_BYTES)
    const { bytesRead } = await handle.read(buffer, 0, HEAD_BYTES, 0)
    return buffer.subarray(0, bytesRead).toString('utf8')
  } finally {
    await handle.close()
  }
}

export class FileRegistry {
  private readonly files = new Map<string, IngestedFile>()
  private readonly byPath = new Map<string, string>()

  async admit(paths: string[]): Promise<InspectResult> {
    const result: InspectResult = { files: [], rejected: [] }
    for (const raw of paths) {
      const name = basename(raw)
      try {
        result.files.push(await this.admitOne(raw))
      } catch (err) {
        result.rejected.push({ name, reason: err instanceof Error ? err.message : String(err) })
      }
    }
    return result
  }

  private async admitOne(raw: string): Promise<IngestedFile> {
    if (!isAbsolute(raw)) throw new Error('not an absolute path')
    const path = resolve(raw)
    const ext = extensionOf(path)
    if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) {
      throw new Error(`unsupported file type ${ext || '(none)'}; expected ${ACCEPTED_EXTENSIONS.join(', ')}`)
    }
    const info = await stat(path)
    if (!info.isFile()) throw new Error('not a regular file')
    if (info.size === 0) throw new Error('file is empty')
    if (info.size > MAX_FILE_BYTES) throw new Error('file is larger than 4 GB')

    const existing = this.byPath.get(path)
    if (existing) {
      const file = this.files.get(existing)!
      file.sizeBytes = info.size
      return file
    }
    const { kind, detail } = classifyFile(path, await readHead(path))
    const file: IngestedFile = {
      id: randomBytes(12).toString('hex'),
      name: basename(path),
      path,
      sizeBytes: info.size,
      kind,
      detail
    }
    this.files.set(file.id, file)
    this.byPath.set(path, file.id)
    return file
  }

  get(id: string): IngestedFile {
    const file = this.files.get(id)
    if (!file) throw new Error('file is no longer available; add it again')
    return file
  }

  forget(id: string): void {
    const file = this.files.get(id)
    if (file) {
      this.files.delete(id)
      this.byPath.delete(file.path)
    }
  }
}
