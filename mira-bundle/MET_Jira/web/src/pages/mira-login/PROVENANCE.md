# Mira login theme provenance

Source: `/home/hal/docker/style/mira-login` (mounted on this host at
`/data/jinx/mnt/65_hal/docker/style/mira-login`).

Last synchronized: 2026-10-22 13:26 (UTC+8).

The following files are upstream-owned and guarded by
`npm run theme:check`:

- `web/public/trace-background.js`
- `tokens.css`
- `login.css`

`login.css` has one intentional host transformation: the Google Fonts
`@import` is replaced by a comment because MET Jira self-hosts Silkscreen
through `@fontsource`.

All MET Jira-specific controls, accessibility fallbacks and visual changes
belong in `integration.css`. Do not add host selectors to the upstream-owned
files.

When synchronizing:

1. Read the upstream `README.md` and `TROUBLESHOOTING_LOG.md`.
2. Copy the three upstream files.
3. Replace only the Google Fonts import in `login.css`.
4. Update the hashes in `web/scripts/check-mira-theme.mjs`.
5. Update the timestamp above.
6. Run `npm run theme:check`, `npm run build` and the login Playwright tests.
