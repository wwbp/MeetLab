import { headers } from 'next/headers';
import { App } from '@/components/agent/app';
import { getAppConfig } from '@/lib/utils';

export default async function AgentPage() {
  const hdrs = await headers();
  const appConfig = await getAppConfig(hdrs);

  return <App appConfig={appConfig} />;
}
