import { ConciergeConsole } from '@/components/desk/concierge-console';

export default function ConsolePage() {
  return <ConciergeConsole tracesUrl={process.env.TRACES_URL} />;
}
