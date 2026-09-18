jest.mock('expo-file-system/legacy', () =>
  require('./support/memfs'),
);

import { memfs } from './support/memfs';
import {
  RetentionError,
  captureDir,
  releaseAttachment,
  releaseCapture,
  retainAttachment,
} from '../files';

const USER_A = 'user-a';
const USER_B = 'user-b';
const CAPTURE_1 = 'capture-1';
const CAPTURE_2 = 'capture-2';

beforeEach(() => memfs.reset());

/**
 * The point of retention: a picked file lives in the OS cache, which the
 * system may reclaim at any moment. Nothing may be recorded in a draft
 * until the bytes are somewhere the app controls.
 */
describe('retainAttachment', () => {
  it('copies the picked file into the account and capture that own it', async () => {
    memfs.put('file:///cache/IMG_1.jpg', 2048);

    const kept = await retainAttachment({
      userId: USER_A,
      captureClientId: CAPTURE_1,
      attachmentId: 'att-1',
      sourceUri: 'file:///cache/IMG_1.jpg',
      name: 'IMG_1.jpg',
      expectedSize: 2048,
    });

    expect(kept.uri).toBe(`${captureDir(USER_A, CAPTURE_1)}att-1.jpg`);
    expect(kept.size).toBe(2048);
    expect(memfs.files.get(kept.uri)).toBe(2048);
    // The original is left alone - the picker owns it.
    expect(memfs.files.get('file:///cache/IMG_1.jpg')).toBe(2048);
  });

  it('survives the source being reclaimed afterwards', async () => {
    memfs.put('file:///cache/voice.m4a', 64);
    const kept = await retainAttachment({
      userId: USER_A,
      captureClientId: CAPTURE_1,
      attachmentId: 'att-voice',
      sourceUri: 'file:///cache/voice.m4a',
      name: 'voice.m4a',
      expectedSize: null,
    });

    // The OS reclaims the cache.
    memfs.files.delete('file:///cache/voice.m4a');

    expect(memfs.files.get(kept.uri)).toBe(64);
  });

  it('refuses - and leaves nothing behind - when the copy fails', async () => {
    memfs.put('file:///cache/big.pdf', 1024);
    memfs.failNextCopy = new Error('ENOSPC: no space left on device');

    await expect(
      retainAttachment({
        userId: USER_A,
        captureClientId: CAPTURE_1,
        attachmentId: 'att-2',
        sourceUri: 'file:///cache/big.pdf',
        name: 'big.pdf',
        expectedSize: 1024,
      }),
    ).rejects.toMatchObject({ name: 'RetentionError', cause: 'copy_failed' });

    expect(memfs.files.has(`${captureDir(USER_A, CAPTURE_1)}att-2.pdf`)).toBe(false);
  });

  it('refuses a short copy rather than keeping part of a file', async () => {
    memfs.put('file:///cache/big.pdf', 1024);
    memfs.shortNextCopyTo = 900;

    const attempt = retainAttachment({
      userId: USER_A,
      captureClientId: CAPTURE_1,
      attachmentId: 'att-3',
      sourceUri: 'file:///cache/big.pdf',
      name: 'big.pdf',
      expectedSize: 1024,
    });

    await expect(attempt).rejects.toBeInstanceOf(RetentionError);
    await expect(attempt).rejects.toMatchObject({ cause: 'size_mismatch' });
    expect(memfs.files.has(`${captureDir(USER_A, CAPTURE_1)}att-3.pdf`)).toBe(false);
  });

  it('keeps one account out of another', async () => {
    memfs.put('file:///cache/a.jpg', 1);
    memfs.put('file:///cache/b.jpg', 1);
    const a = await retainAttachment({
      userId: USER_A, captureClientId: CAPTURE_1, attachmentId: 'x',
      sourceUri: 'file:///cache/a.jpg', name: 'a.jpg', expectedSize: 1,
    });
    const b = await retainAttachment({
      userId: USER_B, captureClientId: CAPTURE_1, attachmentId: 'x',
      sourceUri: 'file:///cache/b.jpg', name: 'b.jpg', expectedSize: 1,
    });
    expect(a.uri).not.toBe(b.uri);
    expect(a.uri).toContain(`/${USER_A}/`);
    expect(b.uri).toContain(`/${USER_B}/`);
  });
});

describe('releasing kept files', () => {
  it('deletes one capture without touching another unsent one', async () => {
    memfs.put('file:///cache/a.jpg', 1);
    memfs.put('file:///cache/b.jpg', 1);
    const first = await retainAttachment({
      userId: USER_A, captureClientId: CAPTURE_1, attachmentId: 'x',
      sourceUri: 'file:///cache/a.jpg', name: 'a.jpg', expectedSize: 1,
    });
    const second = await retainAttachment({
      userId: USER_A, captureClientId: CAPTURE_2, attachmentId: 'y',
      sourceUri: 'file:///cache/b.jpg', name: 'b.jpg', expectedSize: 1,
    });

    await releaseCapture(USER_A, CAPTURE_1);

    expect(memfs.files.has(first.uri)).toBe(false);
    expect(memfs.files.has(second.uri)).toBe(true);
  });

  it('will not delete a path outside its own area', async () => {
    memfs.put('file:///documents/somebody-elses-file', 1);
    await releaseAttachment('file:///documents/somebody-elses-file');
    expect(memfs.files.has('file:///documents/somebody-elses-file')).toBe(true);
  });
});
