import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PersonCard } from './RecordingClient';

describe('PersonCard', () => {
  it('a person without video has a visible card: a border, initials and their name', () => {
    const html = renderToStaticMarkup(<PersonCard name="Ana Lopez" />);
    expect(html).toContain('data-card="true"');
    expect(html).toMatch(/border:\s*3px solid/);
    expect(html).toContain('>AL<');
    expect(html).toContain('>Ana Lopez<');
  });

  it('a person with video shows it, with the name still on the card', () => {
    const html = renderToStaticMarkup(<PersonCard name="Ana" video={<video data-testid="v" />} />);
    expect(html).toContain('data-testid="v"');
    expect(html).toContain('>Ana<');
    expect(html).not.toContain('>A<');
  });
});
