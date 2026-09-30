/**
 * Who a kept-recording action belongs to, decided before the first await.
 *
 * The defect: identity was read AFTER `fileExists`. An account switch
 * landing inside that check made the NEW account look like the
 * initiator, so the ownership test passed against the wrong identity and
 * the share sheet opened on the previous worker's recording. Reading
 * identity first is the whole guarantee, so it is tested directly rather
 * than through the screen.
 *
 * This models the exact shape of the two handlers in
 * app/site-log/index.tsx - `claim` before any await, ownership from the
 * path, re-check before the side effect.
 */
import { isOwnKeptRecordingPath } from '../files';
import { useAuthStore } from '../../store/auth';

type Session = { userId: string | null; sessionNonce: number };

function claim(path: string, read: () => Session) {
  const started = read();
  const owned =
    started.userId !== null && isOwnKeptRecordingPath(path, started.userId);
  return {
    owned,
    stillOurs: () => {
      const now = read();
      return (
        now.userId === started.userId && now.sessionNonce === started.sessionNonce
      );
    },
  };
}

/** The handler, with the account switch landing inside the file check. */
async function exportKept(
  path: string,
  read: () => Session,
  hooks: { duringFileCheck?: () => void } = {},
): Promise<'shared' | 'not-owned' | 'abandoned'> {
  const { owned, stillOurs } = claim(path, read);
  if (!owned) return 'not-owned';

  // The first await. Everything the switch can interleave with is here.
  await Promise.resolve();
  hooks.duringFileCheck?.();
  await Promise.resolve();

  if (!stillOurs()) return 'abandoned';
  return 'shared';
}

const A = 'user-a';
const B = 'user-b';
const PATH_A = `site-log/${A}/oversized/att-1.m4a`;

describe('a kept-recording export belongs to the account that started it', () => {
  it('shares when nothing changed', async () => {
    let s: Session = { userId: A, sessionNonce: 1 };
    await expect(exportKept(PATH_A, () => s)).resolves.toBe('shared');
  });

  it('REGRESSION: a switch during the file check abandons it', async () => {
    // Previously the identity was read here, AFTER the switch, so B
    // looked like the initiator and A's recording was shared under B.
    let s: Session = { userId: A, sessionNonce: 1 };
    const result = await exportKept(PATH_A, () => s, {
      duringFileCheck: () => {
        s = { userId: B, sessionNonce: 2 };
      },
    });
    expect(result).toBe('abandoned');
  });

  it('refuses another account\'s file outright, before any await', async () => {
    let s: Session = { userId: B, sessionNonce: 1 };
    await expect(exportKept(PATH_A, () => s)).resolves.toBe('not-owned');
  });

  it('abandons a sign-out mid-flight even with the same user id', async () => {
    // Same person, new session: tokens were cleared and re-issued, so
    // the nonce moves and the in-flight action does not carry over.
    let s: Session = { userId: A, sessionNonce: 1 };
    const result = await exportKept(PATH_A, () => s, {
      duringFileCheck: () => {
        s = { userId: A, sessionNonce: 2 };
      },
    });
    expect(result).toBe('abandoned');
  });

  it('REGRESSION: a traversal out of my own folder is not mine', async () => {
    // A raw prefix test passes this - it starts with my directory - and
    // it resolves to somebody else's recording. Persisted state is the
    // thing that can be wrong, so the path is canonicalised first.
    let s: Session = { userId: A, sessionNonce: 1 };
    const escaped = `site-log/${A}/oversized/../../${B}/oversized/att.m4a`;
    await expect(exportKept(escaped, () => s)).resolves.toBe('not-owned');
  });

  it('refuses the shapes a real path never has', () => {
    for (const [path, user] of [
      [`site-log/${A}/oversized/../att.m4a`, A],
      [`site-log/${A}/oversized/sub/att.m4a`, A],
      [`site-log/${A}/oversized/`, A],
      [`site-log/${A}/oversized/att.m4a.part`, A], // still being written
      [`site-log/${A}/captures/att.m4a`, A],
      [`site-log/${A}/oversized/att.m4a`, ''],
      [`site-log/../${B}/oversized/att.m4a`, '..'],
    ] as [string, string][]) {
      expect(isOwnKeptRecordingPath(path, user)).toBe(false);
    }
    // ...and accepts the one shape it does produce.
    expect(isOwnKeptRecordingPath(`site-log/${A}/oversized/att-1.m4a`, A)).toBe(true);
  });

  it('REGRESSION: a confirmation answered after a logout does not delete', () => {
    // A native alert outlives a session. The confirm callback runs
    // later, so capturing only the account left the stale dialog with
    // destructive access: the ownership check was handed the CAPTURED
    // identity and agreed with itself.
    let s: Session = { userId: A, sessionNonce: 1 };
    let deleted: string | null = null;

    // The screen's discardKept, in the shape it actually has.
    const startedAs = s.userId;
    const startedUnder = s.sessionNonce;
    const onConfirm = () => {
      if (s.userId !== startedAs || s.sessionNonce !== startedUnder) return;
      deleted = 'att-1';
    };

    // ...dialog open, terminal 401 clears the session...
    s = { userId: null, sessionNonce: 2 };
    onConfirm();
    expect(deleted).toBeNull();

    // ...and the same person signing in again is a NEW session too.
    s = { userId: A, sessionNonce: 3 };
    onConfirm();
    expect(deleted).toBeNull();

    // Unchanged session: it still works, so the guard is not "never".
    s = { userId: A, sessionNonce: 1 };
    onConfirm();
    expect(deleted).toBe('att-1');
  });

  it('the real auth store supplies both fields this relies on', async () => {
    // If either field disappeared, the guard above would silently become
    // a no-op in the screen that uses it.
    const state = useAuthStore.getState();
    expect(state).toHaveProperty('userId');
    expect(typeof state.sessionNonce).toBe('number');
  });
});
