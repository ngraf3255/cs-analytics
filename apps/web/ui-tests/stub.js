import { readFileSync } from 'node:fs';

const fixture = name => JSON.parse(readFileSync(new URL(`../src/test/fixtures/${name}`, import.meta.url), 'utf8'));
const OPTIONS = { maps: ['de_ancient', 'de_mirage', 'cs_rush_001'], weapons: ['ak47', 'm4a1_silencer', 'awp', 'glock'], opening_kill_sides: ['ct', 't'] };

/** Stub the API (VITE_API_BASE_URL) so the checks never depend on the live backend. */
export async function stubApi(page, { signedIn = false } = {}) {
  if (signedIn) await page.addInitScript(() => localStorage.setItem('csa_has_session', '1'));
  await page.route(/^https:\/\/api-site\.csgooner\.com\//, route => {
    const url = new URL(route.request().url());
    const json = body => route.fulfill({ json: body });
    switch (url.pathname) {
      case '/options': return json(OPTIONS);
      case '/steam/status': return json({ enabled: true, steam: true, upload: true, guest: true });
      case '/me': return signedIn ? json(fixture('me.json')) : json({ account: null });
      case '/predict': return json({ predicted_winner: 't', probabilities: { ct: 0.31, t: 0.69 } });
      case '/matches': return json(fixture('matches.json'));
      case '/matches/summary': return json(fixture('summary_personal.json'));
      case '/steam/sync': return json({ ...fixture('me.json').sync, jobs: [] });
      case '/matches/upload': return json({ jobs: [] });
      default: return route.fulfill({ status: 404, json: { detail: 'not_found' } });
    }
  });
}

