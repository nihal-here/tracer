document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("investigate-form");
    const submitBtn = document.getElementById("submit-btn");
    const btnText = document.querySelector(".btn-text");
    const spinner = document.querySelector(".spinner");
    
    const resultsContainer = document.getElementById("results-container");
    const errorContainer = document.getElementById("error-container");
    
    const repoStars = document.getElementById("repo-stars");
    const repoLang = document.getElementById("repo-lang");
    const answerBox = document.getElementById("answer-box");
    const sourcesList = document.getElementById("sources-list");

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const repoUrl = document.getElementById("repo-url").value.trim();
        const question = document.getElementById("question").value.trim();
        
        if (!repoUrl || !question) return;

        // Reset UI
        resultsContainer.classList.add("hidden");
        errorContainer.classList.add("hidden");
        errorContainer.textContent = "";
        
        // Loading State
        submitBtn.disabled = true;
        btnText.classList.add("hidden");
        spinner.classList.remove("hidden");

        try {
            const response = await fetch("/investigate", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    repo: repoUrl,
                    question: question
                })
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "An error occurred during investigation.");
            }

            // Populate Meta
            repoStars.textContent = data.stars.toLocaleString();
            repoLang.textContent = data.language || "Unknown";

            // Populate Answer with Marked.js
            answerBox.innerHTML = marked.parse(data.answer);
            
            // Apply highlight.js to code blocks
            document.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block);
            });

            // Populate Sources
            sourcesList.innerHTML = "";
            if (data.sources && data.sources.length > 0) {
                data.sources.forEach(source => {
                    const li = document.createElement("li");
                    li.textContent = source;
                    sourcesList.appendChild(li);
                });
            }

            // Show Results
            resultsContainer.classList.remove("hidden");

        } catch (error) {
            errorContainer.textContent = error.message;
            errorContainer.classList.remove("hidden");
        } finally {
            // Restore UI
            submitBtn.disabled = false;
            btnText.classList.remove("hidden");
            spinner.classList.add("hidden");
        }
    });
});
