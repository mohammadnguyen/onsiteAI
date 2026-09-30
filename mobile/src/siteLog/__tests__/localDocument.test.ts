import { canonicalLocalPath, isSameLocalFile } from '../localDocument';

const DOC = 'file:///var/mobile/Containers/Data/Application/ABC/Library/Caches/sitelog-ev-1.pdf';

describe('canonicalLocalPath', () => {
  it('returns the absolute path of a plain file URL', () => {
    expect(canonicalLocalPath(DOC)).toBe(
      '/var/mobile/Containers/Data/Application/ABC/Library/Caches/sitelog-ev-1.pdf',
    );
  });

  it('undoes percent-encoding and folds the iOS /private alias', () => {
    expect(canonicalLocalPath('file:///var/x/sitelog-ev-1%2Epdf')).toBe('/var/x/sitelog-ev-1.pdf');
    expect(canonicalLocalPath('file:///private/var/x/a.pdf')).toBe('/var/x/a.pdf');
    // Only the iOS alias folds; a directory that merely starts with
    // "private" is left alone.
    expect(canonicalLocalPath('file:///privateer/var/x/a.pdf')).toBe('/privateer/var/x/a.pdf');
    expect(canonicalLocalPath('file:///private/tmp/a.pdf')).toBe('/private/tmp/a.pdf');
  });

  it('drops query, fragment, empty and dot segments', () => {
    expect(canonicalLocalPath('file:///var//x/./a.pdf?x=1#page=2')).toBe('/var/x/a.pdf');
    expect(canonicalLocalPath('file://localhost/var/x/a.pdf')).toBe('/var/x/a.pdf');
  });

  it('refuses what is not a local file', () => {
    expect(canonicalLocalPath('https://example.com/a.pdf')).toBeNull();
    expect(canonicalLocalPath('http://converter.example/upload')).toBeNull();
    expect(canonicalLocalPath('data:application/pdf;base64,AAAA')).toBeNull();
    expect(canonicalLocalPath('about:blank')).toBeNull();
    expect(canonicalLocalPath('file://evil.example/var/x/a.pdf')).toBeNull();
    expect(canonicalLocalPath('file:var/x/a.pdf')).toBeNull();
  });

  it('refuses traversal, malformed escapes and NUL', () => {
    expect(canonicalLocalPath('file:///var/x/../y/a.pdf')).toBeNull();
    expect(canonicalLocalPath('file:///var/x/%2e%2e/y/a.pdf')).toBeNull();
    expect(canonicalLocalPath('file:///var/x/a%zz.pdf')).toBeNull();
    expect(canonicalLocalPath('file:///var/x/a%00.pdf')).toBeNull();
  });
});

describe('isSameLocalFile', () => {
  it('accepts the opened document, however iOS spells it', () => {
    expect(isSameLocalFile(DOC, DOC)).toBe(true);
    expect(isSameLocalFile(DOC.replace('file:///var/', 'file:///private/var/'), DOC)).toBe(true);
    expect(isSameLocalFile(DOC.replace('.pdf', '%2Epdf'), DOC)).toBe(true);
    expect(isSameLocalFile(`${DOC}#page=3`, DOC)).toBe(true);
  });

  it('refuses a same-named file anywhere else', () => {
    expect(isSameLocalFile('file:///var/other/sitelog-ev-1.pdf', DOC)).toBe(false);
    expect(isSameLocalFile(DOC.replace('/Caches/', '/Caches/sub/'), DOC)).toBe(false);
    expect(isSameLocalFile(DOC.replace('/Library/', '/Documents/'), DOC)).toBe(false);
    // Case matters: the data volume is case-sensitive, and a lookalike is
    // not the same file.
    expect(isSameLocalFile(DOC.replace('sitelog', 'SiteLog'), DOC)).toBe(false);
  });

  it('refuses every other file, scheme and trick', () => {
    expect(isSameLocalFile(DOC.replace('ev-1', 'ev-2'), DOC)).toBe(false);
    expect(isSameLocalFile('file:///var/mobile/x/../Containers/Data/Application/ABC/Library/Caches/sitelog-ev-1.pdf', DOC)).toBe(false);
    expect(isSameLocalFile('https://example.com/sitelog-ev-1.pdf', DOC)).toBe(false);
    expect(isSameLocalFile('file:///documents/site-log/user-a/x/recording.m4a', DOC)).toBe(false);
    expect(isSameLocalFile('', DOC)).toBe(false);
    expect(isSameLocalFile(DOC, '')).toBe(false);
  });
});
