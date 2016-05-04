rtm
===

Various scripts used to feed extra data into RTM tasklist

### token-generator

Generates a token for a given api\_key and shared secret

### gh2rtm

Synchronizes GitHub issues and pull-requests to RTM.

### imap2rtm

Synchronizes emails from an IMAP mailbox to RTM.

Requires a configuration such as:

```yaml
imap:
  host: mail.example.com
  username: me
  password: secret
  folder: INBOX
rtm:
  api_key: secret
  shared_secret: secret
  token: secret
  list: 0123545
  extra_tags:
    - email
```
