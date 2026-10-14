import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { getServerConfig, requireEnv } from './server';

describe('requireEnv', () => {
  it('returns value when defined', () => {
    expect(requireEnv('hello', 'SOME_VAR')).toBe('hello');
  });

  it('throws when value is undefined', () => {
    expect(() => requireEnv(undefined, 'MISSING_VAR')).toThrow('MISSING_VAR is not defined');
  });

  it('throws when value is empty string', () => {
    expect(() => requireEnv('', 'EMPTY_VAR')).toThrow('EMPTY_VAR is not defined');
  });
});

describe('getServerConfig', () => {
  const original = { ...process.env };

  beforeEach(() => {
    delete process.env.LIVEKIT_URL_PUBLIC;
    delete process.env.LIVEKIT_URL_INTERNAL;
    delete process.env.LIVEKIT_URL;
    delete process.env.LIVEKIT_API_KEY;
    delete process.env.LIVEKIT_API_SECRET;
    delete process.env.BOT_RUNNER_URL;
  });

  afterEach(() => {
    Object.assign(process.env, original);
  });

  it('prefers LIVEKIT_URL_PUBLIC over LIVEKIT_URL for livekitUrl', () => {
    process.env.LIVEKIT_URL_PUBLIC = 'wss://public.livekit.cloud';
    process.env.LIVEKIT_URL = 'ws://fallback:7880';
    const config = getServerConfig();
    expect(config.livekitUrl).toBe('wss://public.livekit.cloud');
  });

  it('falls back to LIVEKIT_URL for livekitUrl when no PUBLIC set', () => {
    process.env.LIVEKIT_URL = 'ws://fallback:7880';
    const config = getServerConfig();
    expect(config.livekitUrl).toBe('ws://fallback:7880');
  });

  it('prefers LIVEKIT_URL_INTERNAL for livekitInternalUrl', () => {
    process.env.LIVEKIT_URL_INTERNAL = 'ws://transport-server:7880';
    process.env.LIVEKIT_URL_PUBLIC = 'wss://public.livekit.cloud';
    const config = getServerConfig();
    expect(config.livekitInternalUrl).toBe('ws://transport-server:7880');
  });

  it('falls back to LIVEKIT_URL_PUBLIC for livekitInternalUrl', () => {
    process.env.LIVEKIT_URL_PUBLIC = 'wss://public.livekit.cloud';
    const config = getServerConfig();
    expect(config.livekitInternalUrl).toBe('wss://public.livekit.cloud');
  });

  it('reads LIVEKIT_API_KEY, LIVEKIT_API_SECRET, BOT_RUNNER_URL', () => {
    process.env.LIVEKIT_API_KEY = 'devkey';
    process.env.LIVEKIT_API_SECRET = 'secret';
    process.env.BOT_RUNNER_URL = 'http://agent-runner:7860';
    const config = getServerConfig();
    expect(config.livekitApiKey).toBe('devkey');
    expect(config.livekitApiSecret).toBe('secret');
    expect(config.botRunnerUrl).toBe('http://agent-runner:7860');
  });

  it('returns undefined fields when env vars are absent', () => {
    const config = getServerConfig();
    expect(config.livekitUrl).toBeUndefined();
    expect(config.livekitApiKey).toBeUndefined();
    expect(config.botRunnerUrl).toBeUndefined();
  });
});
