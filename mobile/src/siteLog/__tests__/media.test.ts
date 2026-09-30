import { deriveMediaType } from '../media';

/**
 * This mirrors backend/app/services/evidence.py::derive_media_type. The
 * server compares the class it derives from the uploaded bytes' MIME with
 * the class that was declared and refuses a mismatch - and the declaration
 * is pinned, so a mismatch can never be repaired by retrying.
 */
describe('deriveMediaType mirrors the server', () => {
  it.each([
    ['audio/m4a', 'audio'],
    ['AUDIO/MPEG', 'audio'],
    ['image/jpeg', 'image'],
    ['image/heic', 'image'],
    ['text/plain', 'text'],
    ['text/csv; charset=utf-8', 'text'],
    ['application/pdf', 'document'],
    ['application/octet-stream', 'document'],
    ['video/mp4', 'document'],
    ['', 'document'],
  ])('%s -> %s', (mime, expected) => {
    expect(deriveMediaType(mime)).toBe(expected);
  });
});
