import { Stack } from 'expo-router';
import { tokens } from '../../src/ui/tokens';

export default function AuthLayout() {
  return (
    <Stack
      screenOptions={{
        headerShown: false,
        // F0: this nested group would otherwise fall back to
        // native-stack's default white ground.
        contentStyle: { backgroundColor: tokens.bg },
      }}
    />
  );
}
