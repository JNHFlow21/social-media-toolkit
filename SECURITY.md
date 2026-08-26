# Security Policy

## Supported versions

| Version | Supported |
|---|---:|
| 0.4.x | ✅ |
| 0.3.x and earlier | ❌ Upgrade to the latest release |

The project is currently alpha. Platform breakage and ordinary extraction bugs
are handled through GitHub Issues; vulnerabilities should use the private route
below.

## Report a vulnerability privately

Use
[GitHub private vulnerability reporting](https://github.com/JNHFlow21/social-media-toolkit/security/advisories/new).
Do not open a public issue containing an exploit, secret, private URL, account
identifier, or private media.

Include only what is necessary to reproduce the problem:

- affected release or commit;
- operating system, Python version, Node.js version, and install method;
- affected interface: SDK, CLI, MCP, installer, downloader, or provider route;
- minimal synthetic reproduction and expected impact;
- whether a credential, local file, network boundary, or persistent artifact is
  involved.

Redact secret values and personal data. Maintainers will acknowledge reports on
a best-effort basis and coordinate disclosure after a fix is available.

## Security boundaries

- The toolkit processes public HTTP(S) social links and does not bypass access
  controls.
- It does not require browser cookies or a logged-in browser profile.
- Provider secret values are read from the process environment or an optional
  secret manager and are never included in results.
- Optional TikHub Douyin responses may contain signed, expiring public CDN
  URLs. The toolkit reports their temporary nature and does not persist them
  unless a caller explicitly requests a media download.
- Downloads reject local/private literal IP addresses, cap transfer size,
  sanitize filenames, and record SHA-256 hashes.
- ASR media is temporary. The standard long-recording route also deletes its
  temporary TOS object before returning.
- The project does not collect product telemetry.

These controls reduce risk but do not make arbitrary third-party content or
platform endpoints trusted.
