import { describe, expect, it } from 'vitest';
import { LLM_MODELS, STT_MODELS, TTS_PROVIDERS, type Choice } from './model-choices';
import { EMPTY_CONFIG } from './bot-config';

// Bot settings are read by researchers, not engineers (2026-10-06): every choice is named in
// plain words and says who runs it, so nobody has to decode a model id.
const ALL: [string, Choice[]][] = [['speech-to-text', STT_MODELS], ['language model', LLM_MODELS], ['voice', TTS_PROVIDERS]];

describe.each(ALL)('%s choices', (_, choices) => {
  it('name each choice in plain words, never by its raw id', () => {
    for (const c of choices) {
      const technical = /[\d/-]/.test(c.value); // a plain brand id ('elevenlabs') may be its own name
      if (technical) expect(c.name.toLowerCase()).not.toContain(c.value.toLowerCase());
      expect(c.name).not.toMatch(/[/_]|\d+\.\d+b|-v\d|instruct/i);
    }
  });

  it('say who runs each one: our own server, or a paid service', () => {
    for (const c of choices) expect(c.runs).toMatch(/^(our own server|paid service: \w+)/);
  });

  it('mark the default once, and the default is what an empty config uses', () => {
    expect(choices.filter((c) => c.isDefault)).toHaveLength(1);
    const field = { 'speech-to-text': EMPTY_CONFIG.stt_model, 'language model': EMPTY_CONFIG.llm_model, voice: EMPTY_CONFIG.tts_provider }[_];
    expect(choices.find((c) => c.isDefault)?.value).toBe(field);
  });

  it('have no duplicate ids', () => {
    expect(new Set(choices.map((c) => c.value)).size).toBe(choices.length);
  });
});

it('offers no model v2 does not run (the offline Parakeet variant is served nowhere)', () => {
  expect(STT_MODELS.map((c) => c.value)).not.toContain('parakeet-unified-en-0.6b');
});
