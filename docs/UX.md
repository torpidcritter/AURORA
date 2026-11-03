# Display preferences, shortcuts and notifications

Everything here is saved in the browser (Account → Display and notifications), not on the account, so each device
keeps its own choice. See ADR-049 for the reasoning.

## Theme
Dark (default), Light, or Match system. Change it with the sun or moon button in the top bar or on the account page.

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
