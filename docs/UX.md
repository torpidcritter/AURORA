# Display preferences, shortcuts and notifications

Everything here is saved in the browser (Account → Display and notifications), not on the account, so each device
keeps its own choice. See ADR-049 for the reasoning.

## Theme
Light (default), Dark, or Match system. Change it with the sun or moon button in the top bar or on the account page.

The look is "Ledger" (ADR-051): white paper, ink-navy text and buttons, small square corners, double rules under
table headings, and figures in a monospace (Spline Sans Mono) so columns line up; body text is Instrument Sans. The
dark theme, "Night shift", keeps the same layout with blue-black surfaces and steel-blue actions. Both fonts are
bundled with the app (no request to a font service), under the SIL Open Font License.

Colour has two jobs: the main action (navy, or steel blue in dark) and stock that needs attention (oxblood critical,
burnt orange high, ochre medium, green low). Status always comes with a word or a marker shape, never colour alone.
Charts use grey for actual sales, navy for forecasts and copper for overrides, scenarios and model training; that
trio passes the colour-vision check in both themes. PDF reports use the same ink colours and double rule.

## Keyboard shortcuts
Press `?` anywhere to see them. They are off while you type in a field.

| Keys | Opens |
|---|---|
| `g` `d` | Dashboard |
| `g` `a` | Alerts |
| `g` `p` | Products |
| `g` `f` | Forecasts |
| `g` `r` | Recommendations |
| `g` `k` | Stockout risk |
| `g` `o` | Purchase orders |
| `g` `s` | Suppliers |
| `g` `c` | Assistant |
| `g` `u` | Your account |
| `/` | Search box on the page (products, suppliers) |

## Low-stock notifications
When turned on (the browser asks for permission), an open Aurora tab checks every two minutes for products that
newly reached high or critical stockout risk and shows a system notification; clicking it opens the alerts list.

Limits:
- Nothing arrives when every Aurora tab is closed. Use a daily digest (Digests) for that. Real web push needs a
  service worker and server-held push keys, which are not set up.
- Alerts that were already open when you turned notifications on are not announced again.
- If notifications are blocked for the site, the browser's site settings must allow them again.

## Number and date format
Choose how numbers, amounts and dates are written: English (US, UK, India), German, French, Spanish or Japanese
conventions. The interface text is English only, and amounts are shown in US dollars until organisations can set a
currency.
