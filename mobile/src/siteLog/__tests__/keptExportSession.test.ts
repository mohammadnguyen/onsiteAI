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
import { useAuthStore } from '../../store/auth';

type Session = { userId: string | null; sessionNonce: number };

function claim(path: string, read: () => Session) {
  const started = read();
  const owned =
    started.userId !== null &&
    path.startsWith(`site-log/${started.userId}/oversized/`);
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

  it('the real auth store supplies both fields this relies on', async () => {
    // If either field disappeared, the guard above would silently become
    // a no-op in the screen that uses it.
    const state = useAuthStore.getState();
    expect(state).toHaveProperty('userId');
    expect(typeof state.sessionNonce).toBe('number');
  });
});
