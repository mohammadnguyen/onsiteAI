# Mobile screen pattern

## Purpose

Shape of a new screen file in the Expo / React Native app
(`mobile/app/`). Keeps every screen consistent on safe-area handling,
i18n, data fetching, and the four required UI states. The builder uses
this app one-handed in poor network conditions; consistency is more
important than novelty.

## When To Use

Any new `.tsx` file under `mobile/app/` that renders a screen (an
expo-router route). Subcomponents that don't represent a route live
under `mobile/src/components/`.

## Standard Structure

A screen is a default-exported function component that:

1. Wraps content in `SafeAreaView` from `react-native-safe-area-
   context` with appropriate `edges` (typically `['bottom', 'left',
   'right']` inside the tabs group — the tab bar handles the top
   edge).
2. Reads every visible string via `useTranslation()` and a key under
   `mobile/src/i18n/{en,zh}.json`. No hardcoded English or Chinese
   text in JSX.
3. Reads data via a TanStack Query hook in
   `mobile/src/api/hooks/`. The hook owns the query key, the fetcher,
   and the `enabled` gate on `accessToken`.
4. Renders four explicit states: loading, error, empty, populated.
   None is skipped, even if rare.
5. Wraps any form in `KeyboardAvoidingView` (with `behavior='padding'`
   on iOS) so the keyboard does not cover Submit. See
   `mobile/app/(auth)/login.tsx` for the canonical pattern.
6. Handles sensitive data (auth tokens) only via
   `mobile/src/store/auth.ts`, which uses `expo-secure-store` on
   native and `localStorage` on web.

Canonical examples:

- `mobile/app/(auth)/login.tsx` — form screen with
  `KeyboardAvoidingView`.
- `mobile/app/(tabs)/jobs.tsx` — list with detail modal, loading +
  empty + error states.
- `mobile/app/(tabs)/settings.tsx` — settings + language switcher.

## Rules

- No hardcoded user-facing strings. Every label, placeholder, error,
  and chip text goes through an i18n key.
- No business logic on the device. No GST split, no budget math, no
  FK pre-validation. The backend decides; the screen presents.
- One screen file per route. Subcomponents that grow past ~150 lines
  move to `mobile/src/components/` and import back into the route.
- `testID` on every interactive element. `accessibilityLabel` and
  `accessibilityRole` on buttons and form controls.
- Use `Touchable…` components, not raw `Pressable` styling, unless
  the design genuinely diverges from the standard tap shape.
- Tokens never read from `AsyncStorage`. Native goes through
  `expo-secure-store` (iOS Keychain / Android Keystore); web falls
  back to `localStorage` via the same abstraction.
- Forms send only the fields the user actually set. Sending explicit
  `null` for unset Pydantic fields triggers spurious 422s on the
  backend — build the request body with conditional spreads.

## Anti-Patterns

- Computing the GST split, the budget percentage, or the auto-budget
  in TypeScript. Send the raw inputs to the backend and render the
  computed values it returns.
- Rendering data without a loading state ("if data" with no else).
- Rendering data without an error state.
- Rendering a list without an empty state.
- Storing the access token in `AsyncStorage` or any non-secure store.
- Mixing TanStack Query mutation logic, presentation, and i18n
  switching inside one 300-line file. Split.
- A screen file that imports from another screen file. Shared logic
  belongs in `mobile/src/`.

## Testing Expectations

- `npx tsc --noEmit` clean from `mobile/`.
- `npx expo export --platform web` succeeds (smoke that Metro bundles
  the new screen).
- Manual run on the iOS simulator or Expo Go on a real device,
  covering all four states (loading, error, empty, populated) and the
  EN/ZH language toggle.
- Where the screen calls a mutation (create / update), test inputs
  cover at least: happy path, an actionable 422 from the backend,
  network failure, and any state-specific edge case the screen
  introduces.
- No unit-test framework is wired in the mobile project today.
  Visual + typecheck verification is the contract until a future
  ADR adds component testing.
