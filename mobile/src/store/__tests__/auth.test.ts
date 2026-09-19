import { useAuthStore, userIdFromAccessToken } from '../auth';

/**
 * Knowing WHICH account is signed in without asking the server.
 *
 * The site log has to work on a phone with no signal: unsent captures are
 * per account, so the list screen needs the account id even when /auth/me
 * cannot be reached. The access token already carries it.
 */

/** A token of the shape the backend issues: header.payload.signature. */
function token(claims: Record<string, unknown>): string {
  const b64url = (s: string) =>
    Buffer.from(s, 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url('{"alg":"HS256","typ":"JWT"}')}.${b64url(JSON.stringify(claims))}.signature-not-checked-here`;
}

const USER = '4f2c120f-97ee-434c-adca-ba1828c3312b';

describe('userIdFromAccessToken', () => {
  it('reads the subject of a well-formed token', () => {
    expect(userIdFromAccessToken(token({ sub: USER, exp: 1789 }))).toBe(USER);
  });

  it('gives nothing rather than guessing', () => {
    expect(userIdFromAccessToken(null)).toBeNull();
    expect(userIdFromAccessToken('')).toBeNull();
    expect(userIdFromAccessToken('not-a-token')).toBeNull();
    expect(userIdFromAccessToken('a.b')).toBeNull();
    expect(userIdFromAccessToken('a.!!!not-base64!!!.c')).toBeNull();
    expect(userIdFromAccessToken(token({ exp: 1789 }))).toBeNull();
    expect(userIdFromAccessToken(token({ sub: 42 }))).toBeNull();
  });
});

describe('the auth store follows the account', () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: null, refreshToken: null, userId: null });
  });

  it('knows the account as soon as the tokens are set', async () => {
    await useAuthStore.getState().setTokens(token({ sub: USER }), 'refresh');
    expect(useAuthStore.getState().userId).toBe(USER);
  });

  it('keeps the account across a token refresh', async () => {
    await useAuthStore.getState().setTokens(token({ sub: USER }), 'refresh');
    const before = useAuthStore.getState().sessionNonce;

    await useAuthStore.getState().setAccessToken(token({ sub: USER, exp: 2 }));

    expect(useAuthStore.getState().userId).toBe(USER);
    expect(useAuthStore.getState().sessionNonce).toBe(before);
  });

  it('forgets the account on sign-out', async () => {
    await useAuthStore.getState().setTokens(token({ sub: USER }), 'refresh');
    await useAuthStore.getState().clear();
    expect(useAuthStore.getState().userId).toBeNull();
  });

  it('follows a different account signing in', async () => {
    await useAuthStore.getState().setTokens(token({ sub: USER }), 'refresh-a');
    await useAuthStore.getState().setTokens(token({ sub: 'someone-else' }), 'refresh-b');
    expect(useAuthStore.getState().userId).toBe('someone-else');
  });
});
