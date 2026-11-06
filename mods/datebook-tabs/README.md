# Date-book tabs

A Claude mod that shows the threads of a Claude project as colored, planner-style tabs in a pane docked on the right edge of the screen. Each tab shows the thread's title, a status mark (✎ working, ? needs you, ★ ready for review, ✓ done, ! failed) and the date of its last activity. Click a tab to open that thread. The newest activity is at the top, the list refreshes every minute, and a ↻ Refresh button refreshes on demand. Type `/datebook` to reopen the pane after closing it.

It reads the thread list through the project's own connection, so it only shows tabs inside a Claude project thread session.

## Install

At the prompt of a terminal session:

```
/plugin install datebook-tabs --marketplace mbartole1-design/Gardening
```

Answer `y` to add the marketplace, then pick a scope. From a shell:

```
claude plugin install datebook-tabs --marketplace mbartole1-design/Gardening
```
