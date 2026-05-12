export type ServerConfig = {
  livekitUrl?: string;
  livekitInternalUrl?: string;
  livekitApiKey?: string;
  livekitApiSecret?: string;
  botRunnerUrl?: string;
};

export function getServerConfig(): ServerConfig {
  const publicUrl = process.env.LIVEKIT_URL_PUBLIC ?? process.env.LIVEKIT_URL;
  const internalUrl =
    process.env.LIVEKIT_URL_INTERNAL ?? process.env.LIVEKIT_URL_PUBLIC ?? process.env.LIVEKIT_URL;
  return {
    livekitUrl: publicUrl,
    livekitInternalUrl: internalUrl,
    livekitApiKey: process.env.LIVEKIT_API_KEY,
    livekitApiSecret: process.env.LIVEKIT_API_SECRET,
    botRunnerUrl: process.env.BOT_RUNNER_URL,
  };
}

export function requireEnv(value: string | undefined, name: string): string {
  if (!value) {
    throw new Error(`${name} is not defined`);
  }
  return value;
}
