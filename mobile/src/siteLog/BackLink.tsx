import { Pressable, StyleSheet, Text } from 'react-native';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { tokens } from '../ui/tokens';

/**
 * Back, for the Site Log screens.
 *
 * The root Stack runs with `headerShown: false`, so by convention every
 * pushed screen renders its own back control (app/_layout.tsx says so, and
 * the expense detail screen does it). Without one, returning depends on the
 * iOS edge swipe: undiscoverable, absent on Android, and not an accessible
 * control anywhere.
 *
 * `fallback` covers arriving by deep link, where there is nothing to pop.
 */
export function BackLink({ fallback }: { fallback: string }) {
  const { t } = useTranslation();
  return (
    <Pressable
      onPress={() => {
        if (router.canGoBack()) router.back();
        else router.replace(fallback as never);
      }}
      hitSlop={12}
      accessibilityRole="button"
      accessibilityLabel={t('common.back')}
      testID="site-log-back"
      style={({ pressed }) => [s.btn, pressed ? s.pressed : null]}
    >
      <Text style={s.chevron}>{'\u2039'}</Text>
      <Text style={s.label}>{t('common.back')}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  btn: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    paddingHorizontal: 8,
    paddingVertical: 4,
    minWidth: 72,
  },
  pressed: { opacity: 0.5 },
  chevron: { fontSize: 28, lineHeight: 28, color: tokens.ink, marginRight: 4 },
  label: { fontSize: 16, color: tokens.ink },
});
