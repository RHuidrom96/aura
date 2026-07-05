console.log("password_validation.js loaded");

document.addEventListener("DOMContentLoaded", () => {

    // Add eye toggle to password fields
    const passwordInputs = document.querySelectorAll('input[type="password"]');
    passwordInputs.forEach(input => {
        if (input.id === 'ai_api_key') return;

        const wrapper = document.createElement('div');
        wrapper.style.position = 'relative';
        wrapper.style.display = 'block';
        wrapper.style.width = '100%';

        input.parentNode.insertBefore(wrapper, input);
        wrapper.appendChild(input);
        input.style.paddingRight = '42px';

        // Transfer computed margins to the wrapper to prevent vertical misalignment
        const style = window.getComputedStyle(input);
        const marginBottom = style.marginBottom;
        if (marginBottom && marginBottom !== '0px') {
            wrapper.style.marginBottom = marginBottom;
            input.style.marginBottom = '0';
        }
        const marginTop = style.marginTop;
        if (marginTop && marginTop !== '0px') {
            wrapper.style.marginTop = marginTop;
            input.style.marginTop = '0';
        }

        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'password-toggle-btn';
        toggleBtn.setAttribute('aria-label', 'Show password');
        toggleBtn.style.position = 'absolute';
        toggleBtn.style.top = '0';
        toggleBtn.style.right = '12px';
        toggleBtn.style.height = '100%';
        toggleBtn.style.width = '20px';
        toggleBtn.style.background = 'none';
        toggleBtn.style.border = 'none';
        toggleBtn.style.padding = '0';
        toggleBtn.style.margin = '0';
        toggleBtn.style.color = 'var(--text-faint)';
        toggleBtn.style.cursor = 'pointer';
        toggleBtn.style.display = 'flex';
        toggleBtn.style.alignItems = 'center';
        toggleBtn.style.justifyContent = 'center';
        toggleBtn.style.zIndex = '10';

        const eyeSvg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="eye-icon">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
            </svg>
        `;
        const eyeOffSvg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="eye-off-icon" style="display:none;">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                <line x1="1" y1="1" x2="23" y2="23"/>
            </svg>
        `;

        toggleBtn.innerHTML = eyeSvg + eyeOffSvg;
        wrapper.appendChild(toggleBtn);

        toggleBtn.addEventListener('click', () => {
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            toggleBtn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
            
            const svgEye = toggleBtn.querySelector('.eye-icon');
            const svgEyeOff = toggleBtn.querySelector('.eye-off-icon');
            
            if (show) {
                svgEye.style.display = 'none';
                svgEyeOff.style.display = 'block';
            } else {
                svgEye.style.display = 'block';
                svgEyeOff.style.display = 'none';
            }
            input.focus();
        });
    });

    const password = document.getElementById("password");
    const confirm = document.getElementById("password_confirm");

    // Page doesn't use password validation
    if (!password || !confirm) return;

    const rules = document.getElementById("password-rules");
    const validMessage = document.getElementById("password-valid");
    const match = document.getElementById("password-match");

    const checks = {
        length: document.getElementById("rule-length"),
        lower: document.getElementById("rule-lower"),
        upper: document.getElementById("rule-upper"),
        number: document.getElementById("rule-number"),
        special: document.getElementById("rule-special")
    };

    // ---------- Initial State ----------

    if (rules) rules.hidden = true;
    if (validMessage) validMessage.hidden = true;
    if (match) match.textContent = "";

    // ---------- Helpers ----------

    function updateRule(element, valid) {

        if (!element) return;

        const text = element.textContent.replace(/^[✅❌]\s*/, "");

        element.textContent = (valid ? "✅ " : "❌ ") + text;
        element.style.color = valid ? "#22c55e" : "#ef4444";
    }

    function validatePassword() {

        const value = password.value;

        // Empty field
        if (value.trim() === "") {

            if (rules) rules.hidden = true;
            if (validMessage) validMessage.hidden = true;

            validateMatch();
            return;
        }

        const hasLength = value.length >= 8;
        const hasLower = /[a-z]/.test(value);
        const hasUpper = /[A-Z]/.test(value);
        const hasNumber = /\d/.test(value);
        const hasSpecial = /[^A-Za-z0-9]/.test(value);

        updateRule(checks.length, hasLength);
        updateRule(checks.lower, hasLower);
        updateRule(checks.upper, hasUpper);
        updateRule(checks.number, hasNumber);
        updateRule(checks.special, hasSpecial);

        const allValid =
            hasLength &&
            hasLower &&
            hasUpper &&
            hasNumber &&
            hasSpecial;

        if (allValid) {

            if (rules) rules.hidden = true;
            if (validMessage) validMessage.hidden = false;

        } else {

            if (rules) rules.hidden = false;
            if (validMessage) validMessage.hidden = true;
        }

        validateMatch();
    }

    function validateMatch() {

        if (!match) return;

        if (confirm.value === "") {

            match.textContent = "";
            return;
        }

        if (password.value === confirm.value) {

            match.textContent = "✅ Passwords match";
            match.style.color = "#22c55e";

        } else {

            match.textContent = "❌ Passwords do not match";
            match.style.color = "#ef4444";
        }
    }

    // ---------- Events ----------

    password.addEventListener("focus", () => {

        if (
            password.value.trim() !== "" &&
            validMessage &&
            validMessage.hidden
        ) {
            rules.hidden = false;
        }

    });

    password.addEventListener("input", validatePassword);

    password.addEventListener("blur", () => {

        if (password.value.trim() === "") {

            if (rules) rules.hidden = true;
            if (validMessage) validMessage.hidden = true;
        }

    });

    confirm.addEventListener("input", validateMatch);

});