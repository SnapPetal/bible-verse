import * as fs from 'fs';
import * as path from 'path';

test('the released GitHub deploy role is not synthesized by this app', () => {
  const root = path.resolve(__dirname, '..');
  expect(fs.existsSync(path.join(root, 'lib/github-oidc-stack.ts'))).toBe(false);

  for (const rel of ['bin/bibleverse.ts', 'bin/operator-oidc.ts', 'bin/bibleverse-source-only.ts']) {
    const source = fs.readFileSync(path.join(root, rel), 'utf8');
    expect(source).not.toMatch(/new\s+GithubOidcStack/);
    expect(source).not.toContain("roleName: 'GitHubActionsBibleVerseProdDeploy'");
    expect(source).not.toContain('roleName: "GitHubActionsBibleVerseProdDeploy"');
  }
});
