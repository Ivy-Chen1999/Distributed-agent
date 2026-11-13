import { afterEach, describe, expect, it } from 'vitest';
import { SCREENS } from '../design';
import { DEFAULT_ROLE, ROLES, ROLE_KEY, afterRunScreen, canSee, loadRole, roleOf, roleWith, saveRole, screenFor, screenTitle } from './roles';

afterEach(() => localStorage.clear());

describe('interfaces', () => {
  it('the law and the system together cover every screen; only Run detail is in both', () => {
    const law = roleOf('law').screens;
    const sys = roleOf('system').screens;
    expect(new Set([...law, ...sys])).toEqual(new Set(SCREENS));
    expect(law.filter((s) => sys.includes(s))).toEqual(['detail']);
    for (const r of ROLES) expect(r.screens).toContain(r.home);
  });

  it('the law has no run machinery or team tooling, reads runs as plain analyses, and starts at Regulations', () => {
    const law = roleOf('law');
    expect(law).toMatchObject({ home: 'regs', runMachinery: false, tooling: false, plainAnalysis: true });
    expect(screenTitle(law, 'detail')).toBe('Analysis');
    expect(screenTitle(roleOf('system'), 'detail')).toBe('Run detail');
    expect(canSee(law, 'settings')).toBe(false);
  });

  it('a screen outside the interface opens its home, or the interface that has it', () => {
    const law = roleOf('law');
    expect(screenFor(law, 'agents')).toBe('regs');
    expect(roleWith(law, 'agents').id).toBe('system');
    expect(roleWith(law, 'detail').id).toBe('law'); // in both: stay
    expect(afterRunScreen(law)).toBe('detail');
    expect(afterRunScreen(roleOf('system'))).toBe('pipeline');
  });

  it('roles saved by the earlier five-preset console map onto an interface', () => {
    expect(['full', 'evaluator'].map((r) => roleOf(r).id)).toEqual(['system', 'system']);
    expect(['reader', 'analyst', 'implementer', 'municipal'].map((r) => roleOf(r).id)).toEqual(['law', 'law', 'law', 'law']);
  });

  it('the default is the system interface, and the choice persists', () => {
    expect(roleOf('nope').id).toBe(DEFAULT_ROLE);
    expect(loadRole().id).toBe('system');
    saveRole('law');
    expect(localStorage.getItem(ROLE_KEY)).toBe('law');
    expect(loadRole().id).toBe('law');
  });
});
