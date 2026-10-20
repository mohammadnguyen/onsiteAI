import { isUsableTitle, proposedTitleFrom, titleLength } from '../proposedTitle';

/**
 * The same cases the server's rule is tested with
 * (backend/tests/test_tasks_api.py::test_proposed_title_rule): the app only
 * pre-fills, but the two must agree or every untouched title would be
 * recorded as edited.
 */
describe('proposedTitleFrom', () => {
  const ROOF = 'Engineer asks for extra roof bracing';
  it.each([
    [null, ''],
    ['', ''],
    ['   \n\t\n', ''],
    [ROOF, ROOF],
    ['  first line  \nsecond', 'first line'],
    ['\r\n\r\n  after blanks \r\nmore', 'after blanks'],
    ['old mac\rbreak', 'old mac'],
    ['x'.repeat(250), 'x'.repeat(200)],
    ['y'.repeat(199) + ' z', 'y'.repeat(199)],
  ])('%j -> %j', (body, expected) => {
    expect(proposedTitleFrom(body)).toBe(expected);
  });

  it('counts code points, not UTF-16 units', () => {
    const body = '🏗️'.repeat(150); // two code points each
    const out = proposedTitleFrom(body);
    expect(Array.from(out)).toHaveLength(200);
    expect(out).toBe(Array.from(body).slice(0, 200).join(''));
  });
});

describe('title limits', () => {
  it('blank or over 200 characters cannot be saved', () => {
    expect(isUsableTitle('   ')).toBe(false);
    expect(isUsableTitle('a')).toBe(true);
    expect(isUsableTitle('a'.repeat(200))).toBe(true);
    expect(isUsableTitle('a'.repeat(201))).toBe(false);
    expect(titleLength('  ab  ')).toBe(2);
  });
});
