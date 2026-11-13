# Hosting Aurora for free on one Oracle Cloud server

This runs the whole of Aurora (database, Redis, API, background worker, scheduler, web app and HTTPS) on a single
Oracle Cloud "Always Free" server, using the same `docker-compose.yml` as a local install plus a small HTTPS front
door (`deploy/oracle-free/`). No Vercel, Railway or storage service is needed.

What you get, and its limits:

- Oracle's Always Free Arm allowance (2 CPUs and 12 GB of memory per account since June 2026) runs Aurora
  comfortably for a small team. Model training is CPU-only and slow (minutes per run).
- One server means no redundancy: if it stops, Aurora is down until it restarts. A nightly database backup is kept
  on the same server for 7 days; copy it somewhere else if the data matters (see "Backups" below).
- Oracle may reclaim Always Free servers it considers idle, and new servers sometimes fail with "out of capacity".
  Check Oracle's current Always Free terms before relying on it.
- The `ci.yml` job "Free hosting kit" runs `setup.sh` on a fresh Arm Ubuntu 24.04 machine on every change and signs in
  through HTTPS, so the script itself is tested; Oracle's console steps below are not.

## 1. Accounts (about 15 minutes)

1. **Oracle Cloud**: sign up at <https://www.oracle.com/cloud/free/>. Oracle asks for a card to verify identity; Always
   Free resources are not charged. Pick a home region close to your users (it cannot be changed later).
2. **A web address**: sign in at <https://www.duckdns.org> and add a subdomain, e.g. `acme-aurora` gives
   `acme-aurora.duckdns.org`. Leave the IP empty for now. (Any domain you own works too: point an A record at the server.)

## 2. Create the server

In the Oracle console: **Compute → Instances → Create instance**.

- **Image**: Canonical Ubuntu 24.04 (not "Minimal").
- **Shape**: Ampere → `VM.Standard.A1.Flex`, **2 OCPUs, 12 GB memory** (the whole free allowance).
- **Networking**: keep the defaults (a new virtual cloud network with a public subnet) and *Assign a public IPv4 address*.
- **SSH keys**: *Generate a key pair for me* and **download the private key**. You need it to log in.
- **Boot volume**: the default 50 GB is enough (Always Free includes 200 GB in total).

If it fails with "Out of capacity", try again later or pick another availability domain.

When the instance is running, copy its **Public IP address**, then:

- **DuckDNS**: paste that IP into your subdomain and press *update ip*.
- **Open the web ports**: Instance → *Subnet* link → *Default Security List* → *Add Ingress Rules*, twice:
  source `0.0.0.0/0`, TCP, destination port `80`; and the same for `443`.

## 3. Install Aurora (one command, 20-40 minutes)

Log in from your own computer (macOS/Linux terminal or Windows PowerShell), using the key you downloaded:

```bash
ssh -i path/to/ssh-key.key ubuntu@YOUR_PUBLIC_IP
```

(On macOS/Linux first run `chmod 600 path/to/ssh-key.key`.) Then, on the server:

```bash
git clone https://github.com/torpidcritter/AURORA.git aurora
cd aurora
bash deploy/oracle-free/setup.sh
```

It asks for three things: the web address (`acme-aurora.duckdns.org`), an e-mail for certificate notices, and the
e-mail for the first admin account. Then it:

1. installs Docker and adds swap space,
2. opens ports 80 and 443 in the server's own firewall (Oracle's Ubuntu images block them by default),
3. writes every password and key into `aurora/.env` on the server, generated there and readable only by you,
4. builds and starts Aurora; the `migrate` step creates the database tables,
5. gets an HTTPS certificate from Let's Encrypt automatically,
6. creates the admin account and prints its password,
7. schedules the nightly backup.

Open `https://acme-aurora.duckdns.org`, sign in with the printed password and change it under *Account*. To try it
with sample data: `sudo docker compose -f docker-compose.yml -f deploy/oracle-free/compose.https.yml run --rm api python -m app.cli seed-demo`
(demo data only; it goes into the admin's organisation).

## 4. Everyday operations

All commands run in the `aurora` folder on the server. `DC` below is short for
`sudo docker compose -f docker-compose.yml -f deploy/oracle-free/compose.https.yml`.

| Task | Command |
|---|---|
| Update to the latest version | `bash deploy/oracle-free/update.sh` |
| See what is running | `$DC ps` |
| Follow logs | `$DC logs -f web api worker caddy` |
| Restart everything | `$DC restart` |
| Turn on the full Claude assistant | put the key after `ANTHROPIC_API_KEY=` in `.env`, then `bash deploy/oracle-free/update.sh` |

The server restarts Aurora by itself after a reboot (`restart: unless-stopped`).

## 5. Backups

Every night at 02:30 (server time, UTC) a database backup is written to `/var/backups/aurora/` and backups older than 7
days are removed. They are on the same disk as Aurora, so they protect against mistakes, not against losing the server.
Copy one off the server now and then, from your own computer:

```bash
scp -i path/to/ssh-key.key ubuntu@YOUR_PUBLIC_IP:/var/backups/aurora/aurora-YYYY-MM-DD.dump .
```

(`sudo chmod 755 /var/backups/aurora` first if you want to copy without root.) Restoring follows `docs/RUNBOOK.md`
section 6. Trained model files live in the `artifacts` Docker volume and can be rebuilt by training again.

## 6. Keep in mind

- `.env` holds every secret. Never commit it, paste it into a chat or copy it to another machine you do not control.
- Password resets need an admin to pass on the link; there is no e-mail service (ADR-029).
- Only ports 22, 80 and 443 should be open. The API, database and Redis listen only inside the server.
