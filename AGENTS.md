# AGENTS.MD - Core System Directives & Agent Lifecycle

**CRITICAL INSTRUCTION:** All agents operating within this repository MUST strictly adhere to the following guidelines. Failure to comply violates the core architectural principles of this project. NEVER EVER MOCKUP TEST. TEST SHOULD ALWAYS BE PERFORMED ON LIVE APP/LLM. EVERY TEST WE DO SHOULD GIVE CONFIDENCE THAT APP WILL WORK 100% IN PROD.

## 1. Project Context & Mission
*   **Application Scope:** This is a comprehensive **no-code agent builder application** (alongside other integrated features).
*   **Design Philosophy:** Build features in a clean, simplistic, and intuitive manner. Do not overload the application with unnecessary complexity or cluttered interfaces. 
*   **First Principles Thinking:** When asked to build a feature, always think from first principles. Do not default to boilerplate or generic patterns if they do not fit. Apply innovative, optimized, and best-in-class solutions tailored to the specific problem. Use out of box solutions from various frameworks over custom solution. NEVER TRY TO APPLY WORKAROUND/FALLBACK INSTEAD OF APPLYING FIX TO CORE ISSUE.

## 2. The Multi-Agent Lifecycle 
Application development strictly follows a multi-agent lifecycle. You should launch various subagent for different task. Code cannot be finalized until it successfully passes through this iterative loop:

  **🧪 The Tester:** 
    *   Performs End-to-End (E2E) testing.
    *   *CRITICAL:* UI testing MUST be evaluated directly through **UI snapshots/visual regressions**. Do not rely on DOM-based automation (like Playwright), as it cannot comprehend UI design, visual appeal, or UX accurately.
    *   Refer to `CHROME_BROWSE.md` to **launch the bridge** (run `./scripts/setup_chrome_bridge.sh`), and to `CHROME_WEBTOOL.md` for the operational **Chrome DevTools MCP tool reference** (tool names/signatures, gotchas, login URL, and the single-shot navigation recipe) once the bridge is up.

    YOU SHOULD KEEP IMPROVING UNTIL TESTER HAPPY WITH CHANGE. DO NOT GIVE EXPLCIT CODE CHANGES TO TEST. GIVE THE DETAILS OF THE FUNCTIONAL CHANGE THAT WE ARE PERFORMING.

**The Feedback Loop:** If ANY issue is identified by the Tester , it MUST be sent back to the Developer. This loop continues infinitely until both the Tester explicitly approve the changes.

## 3. Development & Architectural Standards
*   **End-to-End Integration:** Features must never be built in isolation. Ensure full, seamless integration between the frontend and backend for every feature.
*   **Strict Modularity (The 600-Line Rule):** Do not write bulky code. The application must remain highly modular and easy to refactor. **Never exceed 600 lines of code in a single file.** If a file grows larger, break it down into smaller, logical modules.
*   **No Fallbacks or Workarounds:** When asked to fix a bug or issue, you must identify and eliminate the **root cause**. 
    *   Do not leave behind "band-aid" fixes, fallbacks, or temporary workarounds. 
    *   The application must function exactly as expected by targeting the source of the defect.
* **DO NOT PERFORM full integeration testing that is not relevant for the fix. Focus on testing the fixes**

## 4. UI/UX Guidelines
*   **Aesthetic Standard:** The UI must be modern, highly appealing, and user-friendly.
*   **Theming:** All new frontend components must strictly consume and follow the existing theme system (colors, spacing, typography). Do not introduce rogue CSS or hardcoded styles that break the design system.

## 6. Local setup

Our python installed in ~/anaconda3/bin/python3
Some of the modules use reactJs integerated with Flask app and templates. For those, we just need to build the reactJs(without frontend server) 

cd /home/vijay/gitrepo/copilot/text2sql/frontend/agent-app && node -v && npm -v && echo '--- building ---' &&
  npm run build 
