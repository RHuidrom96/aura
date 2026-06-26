console.log("password_validation.js loaded");

document.addEventListener("DOMContentLoaded", () => {

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