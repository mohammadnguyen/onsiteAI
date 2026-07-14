import { Redirect, type Href } from 'expo-router';
import { useAuthStore } from '../src/store/auth';

export default function Index() {
  const { hydrated, accessToken } = useAuthStore();
  if (!hydrated) return null;
  return <Redirect href={accessToken ? '/(tabs)/home' as unknown as Href : '/(auth)/login'} />;
}
