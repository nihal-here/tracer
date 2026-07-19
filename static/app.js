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
    const citationsList = document.getElementById("citations-list");
    const investigationList = document.getElementById("investigation-list");

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const repoUrl = document.getElementById("repo-url").value.trim();
        const question = document.getElementById("question").value.trim();
        
        if (!repoUrl || !question) return;

        // Reset UI
        resultsContainer.classList.add("hidden");
        errorContainer.classList.add("hidden");
        errorContainer.textContent = "";
        citationsList.innerHTML = "";
        investigationList.innerHTML = "";
        
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

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || "An error occurred during investigation.");
            }

            // Read the stream
            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let done = false;
            let fullAnswer = "";
            let buffer = "";

            while (!done) {
                const { value, done: readerDone } = await reader.read();
                done = readerDone;
                if (value) {
                    buffer += decoder.decode(value, { stream: !done });
                    
                    // SSE format is data: {...}\n\n
                    // A chunk might contain multiple data: lines, and might be cut off halfway
                    const lines = buffer.split("\n\n");
                    
                    // The last element is either an empty string (if it ended perfectly with \n\n)
                    // or a partial message. We keep it in the buffer for the next chunk.
                    buffer = lines.pop(); 
                    
                    for (const line of lines) {
                        if (line.startsWith("data: ")) {
                            const dataStr = line.substring(6);
                            try {
                                const data = JSON.parse(dataStr);
                                if (data.metadata) {
                                    // Populate Meta
                                    repoStars.textContent = data.metadata.stars.toLocaleString();
                                    repoLang.textContent = data.metadata.language || "Unknown";
                                    
                                    // Populate Sources
                                    sourcesList.innerHTML = "";
                                    if (data.metadata.sources && data.metadata.sources.length > 0) {
                                        data.metadata.sources.forEach(source => {
                                            const li = document.createElement("li");
                                            li.textContent = source;
                                            sourcesList.appendChild(li);
                                        });
                                    }
                                    
                                    // Show Results instantly
                                    resultsContainer.classList.remove("hidden");
                                }
                                if (data.chunk) {
                                    fullAnswer += data.chunk;
                                    // Populate Answer with Marked.js and sanitize with DOMPurify
                                    const rawHtml = marked.parse(fullAnswer);
                                    answerBox.innerHTML = DOMPurify.sanitize(rawHtml);
                                    
                                    // Apply highlight.js to code blocks
                                    document.querySelectorAll('pre code').forEach((block) => {
                                        hljs.highlightElement(block);
                                    });
                                }
                                if (data.citations) {
                                    citationsList.innerHTML = "";
                                    data.citations.forEach(citation => {
                                        const li = document.createElement("li");
                                        const label = `[${citation.citation_id}] ${citation.path}:L${citation.start_line}-L${citation.end_line}`;
                                        if (citation.url) {
                                            const link = document.createElement("a");
                                            link.href = citation.url;
                                            link.target = "_blank";
                                            link.rel = "noreferrer";
                                            link.textContent = label;
                                            li.appendChild(link);
                                        } else {
                                            li.textContent = label;
                                        }
                                        citationsList.appendChild(li);
                                    });
                                }
                                if (data.investigation_trace) {
                                    investigationList.innerHTML = "";
                                    data.investigation_trace.forEach(step => {
                                        const li = document.createElement("li");
                                        li.textContent = step.result_summary || `${step.tool} completed`;
                                        investigationList.appendChild(li);
                                    });
                                }
                            } catch (err) {
                                console.error("Error parsing JSON chunk", err, dataStr);
                            }
                        }
                    }
                }
            }

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
