/**
 * @jest-environment node
 */

/**
 * The capture flow against a REAL backend.
 *
 * THIS SUITE WRITES. It declares captures, uploads bytes and finalises
 * records, so where it points matters more than what it asserts. The target
 * is resolved by `resolveLiveTarget`, which takes an allow-list and refuses
 * the founder's real backend twice over; read the note there. Credentials
 * come from the environment, never from this file.
 *
 *   SITE_LOG_LIVE_API=https://forey-test-api.fly.dev \
 *   SITE_LOG_LIVE_ADMIN_EMAIL=... SITE_LOG_LIVE_ADMIN_PASSWORD=... \
 *   SITE_LOG_LIVE_OTHER_EMAIL=... SITE_LOG_LIVE_OTHER_PASSWORD=... \
 *   npx jest live
 *
 * Unset SITE_LOG_LIVE_API and the suite does not run, so the ordinary suite
 * stays hermetic. Set it to anything unacceptable and the suite FAILS
 * rather than skipping: a misconfigured run that quietly reports nothing is
 * indistinguishable from a passing one.
 *
 * What this proves that a mocked test cannot: the request shapes this app
 * sends are the ones the server accepts, the two-phase protocol really does
 * end in `complete`, the recovery lookup really finds the record again, and
 * the server really does refuse the things this client is written to avoid.
 *
 * What it does NOT prove: the React Native multipart file part. RN sends a
 * `{ uri, name, type }` object, which only its own networking layer
 * understands; here the same endpoint and field name are exercised with a
 * Node Blob instead. The RN encoding needs a device - see the PR.
 */
import { api } from '../../../api/client';
import {
  liveRunRequested,
  resolveLiveTarget,
  type LiveTarget,
} from './liveTarget';
import {
  declareCapture,
  finalizeCapture,
  findMineByCaptureClientId,
  getEvent,
  listMine,
  type Declaration,
} from '../../../api/siteLog';
import { useAuthStore } from '../../../store/auth';

const REQUESTED = liveRunRequested(process.env);

/**
 * Resolved once, at load. A configuration error becomes a FAILING test
 * below rather than a throw during collection, so the reason is reported
 * instead of the whole file disappearing.
 */
let target: LiveTarget | null = null;
let targetError: Error | null = null;
if (REQUESTED) {
  try {
    target = resolveLiveTarget(process.env);
  } catch (e) {
    targetError = e as Error;
  }
}

const describeLive = target !== null ? describe : describe.skip;

// Point the shared client at the validated target. Without this it would
// use whatever EXPO_PUBLIC_API_URL happens to hold, which is the loophole
// the allow-list exists to close.
if (target !== null) api.defaults.baseURL = target.baseUrl;

const ADMIN = target?.admin ?? { email: '', password: '' };
const OTHER = target?.other ?? { email: '', password: '' };

// A one-pixel PNG: real bytes, real MIME, small enough to be uninteresting.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

let counter = 0;
function uuid(): string {
  counter += 1;
  const rand = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0');
  return `00000000-0000-4000-8000-${rand}${counter.toString(16).padStart(4, '0')}`;
}

async function signIn(who: { email: string; password: string }): Promise<void> {
  const r = await api.post('/auth/login', who);
  await useAuthStore.getState().setTokens(r.data.access_token, r.data.refresh_token);
}

/** The same route and field name the app uses, with a Node file part. */
async function putFile(
  eventId: string,
  attachmentId: string,
  bytes: Buffer,
  filename: string,
  mime: string,
) {
  const form = new FormData();
  form.append('file', new Blob([new Uint8Array(bytes)], { type: mime }), filename);
  return await api.put(
    `/site-log-events/${eventId}/attachments/${attachmentId}`,
    form,
    { headers: { 'Content-Type': undefined } },
  );
}

function declaration(over: Partial<Declaration> = {}): Declaration {
  return {
    capture_client_id: uuid(),
    job_id: null,
    occurred_at: null,
    internal_location: null,
    body_text: 'Poured 12m3 bay 3',
    attachments: [],
    ...over,
  };
}

describeLive('against a live backend', () => {
  beforeAll(async () => {
    await signIn(ADMIN);
  });

  it('text only: declares, stores the text server-side, and completes', async () => {
    const d = declaration();
    const event = await declareCapture(d);

    expect(event.capture_client_id).toBe(d.capture_client_id);
    expect(event.revision.body_text).toBe('Poured 12m3 bay 3');
    // The server owns the text row and uploads it itself.
    const inline = event.attachments.filter(
      (a) => !d.attachments.some((x) => x.attachment_client_id === a.attachment_client_id),
    );
    expect(inline).toHaveLength(1);
    expect(inline[0].declared_media_type).toBe('text');
    expect(inline[0].state).toBe('stored');
    // And says so: the detail screen hides exactly this row on the flag,
    // never on the media type.
    expect(inline[0].is_inline_text).toBe(true);

    const finalized = await finalizeCapture(event.site_log_event_id);
    expect(finalized.capture_status).toBe('complete');
  });

  it('text and a photo: uploads, finalizes, and reads back', async () => {
    const attachmentId = uuid();
    const d = declaration({
      attachments: [
        { attachment_client_id: attachmentId, declared_media_type: 'image', declared_size_bytes: PNG.length },
      ],
    });

    const event = await declareCapture(d);
    expect(event.capture_status).toBe('pending_upload');

    const put = await putFile(event.site_log_event_id, attachmentId, PNG, 'bay3.png', 'image/png');
    expect(put.status).toBe(201);
    expect(put.data.state).toBe('stored');

    const finalized = await finalizeCapture(event.site_log_event_id);
    expect(finalized.capture_status).toBe('complete');

    const reopened = await getEvent(event.site_log_event_id);
    expect(reopened.attachments.every((a) => a.state === 'stored')).toBe(true);
    const photo = reopened.attachments.find((a) => a.attachment_client_id === attachmentId);
    expect(photo?.evidence_id).toBeTruthy();
    // The user's photo is not the server's text row, and the server says
    // which is which.
    expect(photo?.is_inline_text).toBe(false);
    expect(reopened.attachments.filter((a) => a.is_inline_text === true)).toHaveLength(1);
  });

  it('a replayed declaration returns the same record, not a second one', async () => {
    const d = declaration();
    const first = await declareCapture(d);
    const again = await declareCapture(d);

    expect(again.site_log_event_id).toBe(first.site_log_event_id);

    const mine = await listMine(0, 50);
    const matches = mine.filter((e) => e.capture_client_id === d.capture_client_id);
    expect(matches).toHaveLength(1);
  });

  it('finds a record again by capture_client_id after a lost answer', async () => {
    const d = declaration({ body_text: 'Lost the answer to this one' });
    const created = await declareCapture(d);

    // What the client does when it never saw the response.
    const found = await findMineByCaptureClientId(d.capture_client_id);

    expect(found?.site_log_event_id).toBe(created.site_log_event_id);
    expect(found?.revision.body_text).toBe('Lost the answer to this one');
  });

  it('refuses a client upload to the reserved inline row', async () => {
    const d = declaration();
    const event = await declareCapture(d);
    const inlineId = event.attachments[0].attachment_client_id;

    await expect(
      putFile(event.site_log_event_id, inlineId, Buffer.from('rewritten'), 'x.txt', 'text/plain'),
    ).rejects.toMatchObject({ response: { status: 422 } });

    // The text the record carries is untouched.
    const after = await getEvent(event.site_log_event_id);
    expect(after.revision.body_text).toBe('Poured 12m3 bay 3');
  });

  it('refuses an upload whose media class does not match the declaration', async () => {
    // Exactly the defect the client's deriveMediaType exists to avoid: a
    // picked image declared as `document` can never be uploaded, because the
    // declaration is pinned.
    const attachmentId = uuid();
    const d = declaration({
      attachments: [
        { attachment_client_id: attachmentId, declared_media_type: 'document', declared_size_bytes: PNG.length },
      ],
    });
    const event = await declareCapture(d);

    await expect(
      putFile(event.site_log_event_id, attachmentId, PNG, 'bay3.png', 'image/png'),
    ).rejects.toMatchObject({ response: { status: 422 } });
  });

  it('shows each author only their own records', async () => {
    const d = declaration({ body_text: "The admin's own note" });
    const created = await declareCapture(d);

    await signIn(OTHER);
    const theirs = await listMine(0, 50);
    expect(theirs.some((e) => e.site_log_event_id === created.site_log_event_id)).toBe(false);
    // Not merely absent from the list: unreadable, and hidden as a 404
    // rather than a 403, so existence itself does not leak.
    await expect(getEvent(created.site_log_event_id)).rejects.toMatchObject({
      response: { status: 404 },
    });

    await signIn(ADMIN);
    const mine = await listMine(0, 50);
    expect(mine.some((e) => e.site_log_event_id === created.site_log_event_id)).toBe(true);
  });

  it('pages without overlap and refuses an oversized page', async () => {
    const first = await listMine(0, 5);
    const second = await listMine(5, 5);
    const ids = new Set([...first, ...second].map((e) => e.site_log_event_id));
    expect(ids.size).toBe(first.length + second.length);

    await expect(listMine(0, 500)).rejects.toMatchObject({ response: { status: 422 } });
  });
});

describe('the live suite itself', () => {
  it('refuses to run against an unacceptable target', () => {
    // A live run was asked for and could not be pointed somewhere safe.
    // This FAILS - it does not skip - because a misconfigured write suite
    // that reports nothing reads exactly like one that passed.
    if (REQUESTED && targetError !== null) {
      throw targetError;
    }
    expect(targetError).toBeNull();
  });

  it('says plainly when it did not run', () => {
    if (!REQUESTED) {
      // Not a silent skip: an unrun check must never read as a passing one.
      // eslint-disable-next-line no-console
      console.log(
        'live capture tests DID NOT RUN - no SITE_LOG_LIVE_API. These 8 ' +
          'tests prove nothing about this commit unless they are run ' +
          'against the Forey Test backend.',
      );
    }
    expect(true).toBe(true);
  });
});
