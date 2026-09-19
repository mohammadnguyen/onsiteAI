jest.mock('expo-file-system/legacy', () =>
  require('./support/memfs'),
);

import { memfs, setDocumentDirectory } from './support/memfs';
import {
  RetentionError,
  canonicalFileUri,
  captureDir,
  releaseAttachment,
  releaseCapture,
  pathUnderDocuments,
  planRetention,
  retainAttachment,
  retainedUri,
  sameFile,
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

describe('verifying the copy when the picker gave no size', () => {
  it('refuses a truncated recording, whose size is never known in advance', async () => {
    // Every recording arrives with expectedSize null: the recorder does not
    // report one. Comparing only against the picker's number therefore
    // checked nothing at all for exactly the file that cannot be picked
    // again.
    memfs.put('file:///cache/voice.m4a', 5000);
    memfs.shortNextCopyTo = 120;

    await expect(
      retainAttachment({
        userId: USER_A,
        captureClientId: CAPTURE_1,
        attachmentId: 'att-voice',
        sourceUri: 'file:///cache/voice.m4a',
        name: 'voice.m4a',
        expectedSize: null,
      }),
    ).rejects.toMatchObject({ name: 'RetentionError', cause: 'size_mismatch' });

    expect(memfs.files.has(`${captureDir(USER_A, CAPTURE_1)}att-voice.m4a`)).toBe(false);
  });

  it('refuses when the copy cannot be measured at all', async () => {
    // "We could not check" is not "it is fine". An unmeasurable copy is not
    // recorded as kept.
    memfs.put('file:///cache/photo.jpg', 40);
    memfs.hideSizeOf = `${captureDir(USER_A, CAPTURE_1)}att-x.jpg`;

    await expect(
      retainAttachment({
        userId: USER_A,
        captureClientId: CAPTURE_1,
        attachmentId: 'att-x',
        sourceUri: 'file:///cache/photo.jpg',
        name: 'photo.jpg',
        expectedSize: 40,
      }),
    ).rejects.toMatchObject({ name: 'RetentionError' });
  });
});

describe('when iOS moves the app container', () => {
  it('still finds the kept file, because the path is stored relative', async () => {
    memfs.put('file:///cache/IMG_9.jpg', 120);
    const kept = await retainAttachment({
      userId: USER_A,
      captureClientId: CAPTURE_1,
      attachmentId: 'att-9',
      sourceUri: 'file:///cache/IMG_9.jpg',
      name: 'IMG_9.jpg',
      expectedSize: 120,
    });
    expect(kept.path).toBe(`site-log/${USER_A}/${CAPTURE_1}/att-9.jpg`);

    // An OS update or a restore from backup: Documents survives, its
    // absolute path does not.
    const moved = 'file:///containers/NEW-UUID/Documents/';
    memfs.files.delete(kept.uri);
    memfs.put(`${moved}${kept.path}`, 120);
    setDocumentDirectory(moved);

    const now = retainedUri(kept.path);
    expect(now).toBe(`${moved}${kept.path}`);
    expect(memfs.files.has(now as string)).toBe(true);
    // The URI recorded before the move points at nothing, which is exactly
    // why it is not what gets used.
    expect(memfs.files.has(kept.uri)).toBe(false);
  });
});

describe('adopting a path recorded by an older build', () => {
  const owner = { userId: USER_A, captureClientId: CAPTURE_1 };

  it('accepts the capture\'s own file, moved container or not', () => {
    expect(
      pathUnderDocuments(`file:///documents/site-log/${USER_A}/${CAPTURE_1}/att-1.jpg`, owner),
    ).toBe(`site-log/${USER_A}/${CAPTURE_1}/att-1.jpg`);
    expect(
      pathUnderDocuments(
        `file:///containers/OLD/Documents/site-log/${USER_A}/${CAPTURE_1}/att-1.jpg`,
        owner,
      ),
    ).toBe(`site-log/${USER_A}/${CAPTURE_1}/att-1.jpg`);
  });

  it('refuses anything outside this account and this capture', () => {
    // The stored string is old persisted state; the folder layout is the
    // only thing keeping one account's files from another's.
    expect(
      pathUnderDocuments(`file:///documents/site-log/${USER_B}/${CAPTURE_1}/att-1.jpg`, owner),
    ).toBeNull();
    expect(
      pathUnderDocuments(`file:///documents/site-log/${USER_A}/${CAPTURE_2}/att-1.jpg`, owner),
    ).toBeNull();
    expect(
      pathUnderDocuments(
        `file:///containers/OLD/Documents/site-log/${USER_B}/${CAPTURE_1}/att-1.jpg`,
        owner,
      ),
    ).toBeNull();
    // Never ours at all, and no traversal out of the folder.
    expect(pathUnderDocuments('file:///cache/IMG_1.jpg', owner)).toBeNull();
    expect(
      pathUnderDocuments(`file:///documents/site-log/${USER_A}/${CAPTURE_1}/../x.jpg`, owner),
    ).toBeNull();
    expect(
      pathUnderDocuments(`file:///documents/site-log/${USER_A}/${CAPTURE_1}/sub/x.jpg`, owner),
    ).toBeNull();
  });
});

describe('what a file URI means', () => {
  it('reduces equivalent spellings to one form', () => {
    const plain = 'file:///documents/site-log/user-a/capture-1/att-1.jpg';
    expect(canonicalFileUri(plain)).toBe(plain);
    expect(canonicalFileUri('file:///documents/site-log/user-a/./capture-1/att-1.jpg')).toBe(plain);
    expect(canonicalFileUri('file:///documents//site-log/user-a/capture-1/att-1.jpg')).toBe(plain);
    expect(canonicalFileUri('file:///documents/site-log/user-a/capture-1/att%2D1.jpg')).toBe(plain);
    expect(canonicalFileUri('file://localhost/documents/site-log/user-a/capture-1/att-1.jpg')).toBe(
      plain,
    );
    expect(canonicalFileUri('file:///documents/x/../site-log/user-a/capture-1/att-1.jpg')).toBe(
      plain,
    );
    expect(sameFile(plain, 'file:///documents/site-log/user-a/./capture-1/att-1.jpg')).toBe(true);
  });

  it('refuses what it cannot decide', () => {
    // Undecidable is not "probably fine": every one of these must stop the
    // caller from copying, uploading or deleting.
    expect(canonicalFileUri('content://media/external/images/1')).toBeNull();
    expect(canonicalFileUri('file:///%E0%A4%A')).toBeNull();      // malformed encoding
    expect(canonicalFileUri('file:///..')).toBeNull();            // above the root
    expect(canonicalFileUri('file:///documents/%252e%252e/x')).toBeNull(); // double-encoded
    expect(canonicalFileUri('')).toBeNull();
  });
});

describe('deciding what to do with one attachment', () => {
  const owner = { userId: USER_A, captureClientId: CAPTURE_1 };
  const target = `${captureDir(USER_A, CAPTURE_1)}att-1.jpg`;

  it('adopts a file that is already exactly where it belongs', () => {
    expect(
      planRetention({ sourceUri: target, owner, attachmentId: 'att-1', name: 'att-1.jpg' }),
    ).toEqual({ action: 'adopt', uri: target, path: `site-log/${USER_A}/${CAPTURE_1}/att-1.jpg` });
  });

  it('adopts it through an equivalent spelling, rather than copying it onto itself', () => {
    const dotted = `file:///documents/site-log/${USER_A}/./${CAPTURE_1}/att-1.jpg`;
    const encoded = `file:///documents/site-log/${USER_A}/${CAPTURE_1}/att%2D1.jpg`;
    for (const spelling of [dotted, encoded]) {
      expect(
        planRetention({ sourceUri: spelling, owner, attachmentId: 'att-1', name: 'att-1.jpg' })
          .action,
      ).toBe('adopt');
    }
  });

  it('copies a file that was never ours', () => {
    const plan = planRetention({
      sourceUri: 'file:///cache/IMG_1.jpg',
      owner,
      attachmentId: 'att-1',
      name: 'IMG_1.jpg',
    });
    expect(plan).toMatchObject({ action: 'copy', from: 'file:///cache/IMG_1.jpg', to: target });
  });

  it('refuses another capture, however the path is spelled', () => {
    const spellings = [
      `file:///documents/site-log/${USER_B}/${CAPTURE_1}/att-1.jpg`,
      `file:///documents/site-log/${USER_A}/${CAPTURE_2}/att-1.jpg`,
      `file:///documents/site-log/${USER_A}/${CAPTURE_1}/../${CAPTURE_2}/att-1.jpg`,
      `file:///documents/site-log/${USER_A}/${CAPTURE_1}/%2e%2e/${CAPTURE_2}/att-1.jpg`,
      `file:///documents/./site-log/${USER_B}/./${CAPTURE_1}/att-1.jpg`,
    ];
    for (const spelling of spellings) {
      expect(
        planRetention({ sourceUri: spelling, owner, attachmentId: 'att-1', name: 'att-1.jpg' }),
      ).toEqual({ action: 'refuse', reason: 'not_ours' });
    }
  });

  it('refuses anything undecidable', () => {
    expect(
      planRetention({
        sourceUri: 'content://media/external/images/1',
        owner,
        attachmentId: 'att-1',
        name: 'a.jpg',
      }),
    ).toEqual({ action: 'refuse', reason: 'undecidable' });
  });
});

describe('copying a file onto itself', () => {
  it('is refused outright, because the platform would delete it first', async () => {
    const target = `${captureDir(USER_A, CAPTURE_1)}att-1.jpg`;
    memfs.put(target, 4096);

    await expect(
      retainAttachment({
        userId: USER_A,
        captureClientId: CAPTURE_1,
        attachmentId: 'att-1',
        sourceUri: target,
        name: 'att-1.jpg',
        expectedSize: 4096,
      }),
    ).rejects.toMatchObject({ name: 'RetentionError', cause: 'self_copy' });

    // Still there. This is the assertion that matters: the mocked copy
    // deletes its destination first, exactly as the native one does.
    expect(memfs.files.get(target)).toBe(4096);
  });

  it('is refused through an equivalent spelling too', async () => {
    const target = `${captureDir(USER_A, CAPTURE_1)}att-1.jpg`;
    memfs.put(target, 4096);

    await expect(
      retainAttachment({
        userId: USER_A,
        captureClientId: CAPTURE_1,
        attachmentId: 'att-1',
        sourceUri: `file:///documents/site-log/${USER_A}/./${CAPTURE_1}/att-1.jpg`,
        name: 'att-1.jpg',
        expectedSize: 4096,
      }),
    ).rejects.toMatchObject({ cause: 'self_copy' });
    expect(memfs.files.get(target)).toBe(4096);
  });

  it('cleans up after a failed copy without touching the source', async () => {
    memfs.put('file:///cache/IMG_2.jpg', 2048);
    memfs.failNextCopy = new Error('ENOSPC: no space left on device');

    await expect(
      retainAttachment({
        userId: USER_A,
        captureClientId: CAPTURE_1,
        attachmentId: 'att-2',
        sourceUri: 'file:///cache/IMG_2.jpg',
        name: 'IMG_2.jpg',
        expectedSize: 2048,
      }),
    ).rejects.toMatchObject({ cause: 'copy_failed' });

    expect(memfs.files.get('file:///cache/IMG_2.jpg')).toBe(2048);
    expect(memfs.files.has(`${captureDir(USER_A, CAPTURE_1)}att-2.jpg`)).toBe(false);
  });
});
