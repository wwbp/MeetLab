import { describe, expect, it } from 'vitest';
import { validateLiveKitPublicUrlForRequestHost } from './validateLiveKitPublicUrl';

describe('validateLiveKitPublicUrlForRequestHost', () => {
  it('accepts localhost request host with localhost livekit url', () => {
    const result = validateLiveKitPublicUrlForRequestHost(
      'ws://localhost:7880',
      'localhost:3000',
    );
    expect(result).toBeNull();
  });

  it('rejects external request host when livekit public url is localhost', () => {
    const result = validateLiveKitPublicUrlForRequestHost(
      'ws://localhost:7880',
      'abc123.ngrok-free.app',
    );
    expect(result).toContain('LIVEKIT_URL_PUBLIC resolves to localhost');
  });

  it('rejects external request host when livekit public url is loopback ip', () => {
    const result = validateLiveKitPublicUrlForRequestHost(
      'ws://127.0.0.1:7880',
      'demo.example.com',
    );
    expect(result).toContain('LIVEKIT_URL_PUBLIC resolves to 127.0.0.1');
  });

  it('accepts external request host when livekit public url is public', () => {
    const result = validateLiveKitPublicUrlForRequestHost(
      'wss://my-project.livekit.cloud',
      'abc123.ngrok-free.app',
    );
    expect(result).toBeNull();
  });

  it('returns validation error for malformed livekit url', () => {
    const result = validateLiveKitPublicUrlForRequestHost(
      'not-a-url',
      'abc123.ngrok-free.app',
    );
    expect(result).toContain('LIVEKIT_URL_PUBLIC is invalid');
  });
});
