# Contributing

Thanks for helping improve the bot.

1. Open an issue before a large behavioral or storage-contract change.
2. Fork the repository and create a focused branch.
3. Keep provider-specific deployment code outside this application repository.
4. Add or update tests.
5. Run:

   ```sh
   make test
   make fmt
   git diff --check
   ```

6. Open a pull request describing the user-visible behavior, privacy impact,
   and any migration requirements.

Never include real Signal messages, phone numbers, UUIDs, group identifiers,
calendar data, credentials, or Signal identity files in issues, tests, logs, or
pull requests.
