# Campus Onboarding Copilot instructions

## Responsive UI quality gate

Every user-interface change must be designed, implemented, and validated for
both desktop and phone layouts. Mobile is a blocking product surface, not a
secondary adaptation: a UI task is incomplete if the phone experience has
clipping, partial controls, unintended scrolling, unsafe-area collisions,
missing disclosures, inconsistent behavior, or weaker accessibility than the
desktop experience.

For every UI change:

1. Define the intended desktop and mobile behavior before editing.
2. Preserve feature parity unless a deliberate platform-specific difference is
   documented.
3. Add or update regression tests for both responsive contracts.
4. Inspect the live app at a desktop viewport and at a phone viewport near
   390 by 844 CSS pixels.
5. Verify dynamic states, focus states, drawers, carousels, and the composer—not
   only the initial screenshot.
6. Confirm there is no horizontal page overflow and no clipped or partially
   visible interactive element.

Do not report a UI change complete until both surfaces pass these checks.
