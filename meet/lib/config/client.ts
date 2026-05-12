export type ClientConfig = {
  connectionDetailsEndpoint: string;
  inDevelopment: boolean;
};

export function getClientConfig(): ClientConfig {
  return {
    connectionDetailsEndpoint:
      process.env.NEXT_PUBLIC_CONN_DETAILS_ENDPOINT ?? '/api/agent-connection',
    inDevelopment: process.env.NODE_ENV !== 'production',
  };
}
