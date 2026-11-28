# Security policy

pal holds private data (conversations, memories, documents), so security reports are taken seriously.

## Supported versions

Only the latest code on the `main` branch is supported. There are no separate releases with their own fixes.

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately through GitHub instead: go to the repository's **Security** tab, then **Report a vulnerability**. ([direct link](https://github.com/ericckzhou/pal/security/advisories/new))

Include:

- what the problem is and what an attacker could do with it
- steps to reproduce it, or a proof of concept
- the version (commit) and setup you used: Windows or Docker, local or over Tailscale

pal is maintained by one person in their spare time. I'll aim to acknowledge a report within 7 days and keep you updated until it's fixed. Once a fix is on `main`, I'm happy to credit you in the advisory if you'd like.

## What's in scope

Problems in pal's own code, for example:

- data leaving the machine without the owner opting in (getting past the egress guard)
- signing in, pairing a device or using a session without permission
- one device reaching owner-only features (Settings, Logs, device admin, folder ingestion)
- reading data from backups or kept uploads without the key
- injection (SQL, prompt-to-action) that changes data without the owner confirming
- cross-site attacks against the web app (XSS, CSRF, Host or Origin header tricks)
- StarcallOS library text being stored by pal, reaching Anthropic without both of that chat's switches on, or being searched in a chat that didn't turn it on
- setting the StarcallOS server path from anywhere but a browser on the PC

## What's out of scope

- attacks that need admin or physical access to the owner's PC
- what the StarcallOS server can do once started: pal runs it as the owner's Windows user with no sandbox, as trusted local code at a path the owner chose (report StarcallOS bugs to [its repo](https://github.com/ericckzhou/StarcallOS/security))
- problems in Ollama, PostgreSQL, Docker, Tailscale or other dependencies: please report those upstream (a note to me is still welcome if pal uses them unsafely)
- the [public demo](https://pal-assistant-demo.vercel.app), which runs only made-up data in the browser with no backend
- the limits pal already documents in the README's [Privacy and security](README.md#privacy-and-security) section, such as the egress guard covering only pal's own process
