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
            let citationMap = {};

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

                                    // Show Results instantly
                                    resultsContainer.classList.remove("hidden");
                                }
                                if (data.chunk) {
                                    fullAnswer += data.chunk;
                                    // Populate Answer with Marked.js and sanitize with DOMPurify
                                    const rawHtml = marked.parse(fullAnswer);
                                    const cleanHtml = DOMPurify.sanitize(rawHtml);

                                    const tempDiv = document.createElement('div');
                                    tempDiv.innerHTML = cleanHtml;

                                    try {
                                        function linkifyTextNodes(node) {
                                            if (node.nodeType === 3) { // Node.TEXT_NODE
                                                const parentTag = (node.parentNode && node.parentNode.tagName) ? node.parentNode.tagName.toLowerCase() : '';
                                                if (parentTag === 'code' || parentTag === 'pre' || parentTag === 'a') return;

                                                const text = node.nodeValue;
                                                const regex = /\[([\d\s,]+)\]/g;
                                                let match;
                                                let lastIndex = 0;
                                                let hasMatch = false;
                                                const fragments = [];

                                                while ((match = regex.exec(text)) !== null) {
                                                    hasMatch = true;
                                                    const innerText = match[1];

                                                    if (match.index > lastIndex) {
                                                        fragments.push(document.createTextNode(text.substring(lastIndex, match.index)));
                                                    }

                                                    fragments.push(document.createTextNode("["));
                                                    const parts = innerText.split(",");
                                                    for (let i = 0; i < parts.length; i++) {
                                                        const part = parts[i];
                                                        const idMatch = part.match(/(\s*)(\d+)(\s*)/);
                                                        if (idMatch) {
                                                            const leading = idMatch[1];
                                                            const id = idMatch[2];
                                                            const trailing = idMatch[3];

                                                            if (leading) fragments.push(document.createTextNode(leading));
                                                            if (citationMap[id]) {
                                                                const a = document.createElement('a');
                                                                a.href = citationMap[id];
                                                                a.target = "_blank";
                                                                a.rel = "noreferrer";
                                                                a.textContent = id;
                                                                fragments.push(a);
                                                            } else {
                                                                fragments.push(document.createTextNode(id));
                                                            }
                                                            if (trailing) fragments.push(document.createTextNode(trailing));
                                                        } else {
                                                            fragments.push(document.createTextNode(part));
                                                        }
                                                        if (i < parts.length - 1) {
                                                            fragments.push(document.createTextNode(","));
                                                        }
                                                    }
                                                    fragments.push(document.createTextNode("]"));
                                                    lastIndex = regex.lastIndex;
                                                }

                                                if (hasMatch) {
                                                    if (lastIndex < text.length) {
                                                        fragments.push(document.createTextNode(text.substring(lastIndex)));
                                                    }
                                                    const parent = node.parentNode;
                                                    if (parent) {
                                                        fragments.forEach(frag => parent.insertBefore(frag, node));
                                                        parent.removeChild(node);
                                                    }
                                                }
                                            } else if (node.nodeType === 1) { // Node.ELEMENT_NODE
                                                Array.from(node.childNodes).forEach(linkifyTextNodes);
                                            }
                                        }

                                        linkifyTextNodes(tempDiv);
                                    } catch (err) {
                                        console.error("Citation linkification failed", err);
                                    }

                                    answerBox.innerHTML = '';
                                    while (tempDiv.firstChild) {
                                        answerBox.appendChild(tempDiv.firstChild);
                                    }

                                    // Apply highlight.js to code blocks
                                    try {
                                        answerBox.querySelectorAll('pre code').forEach((block) => {
                                            hljs.highlightElement(block);
                                        });
                                    } catch (err) {
                                        console.error("Highlighting failed", err);
                                    }
                                }
                                if (data.citations) {
                                    citationsList.innerHTML = "";
                                    data.citations.forEach(citation => {
                                        if (citation.url) {
                                            citationMap[citation.citation_id] = citation.url;
                                        }
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
                                    const investigationPanel = investigationList.closest('.sources-section');
                                    if (data.investigation_trace.length === 0) {
                                        if (investigationPanel) investigationPanel.style.display = 'none';
                                    } else {
                                        if (investigationPanel) investigationPanel.style.display = 'block';
                                        investigationList.innerHTML = "";
                                        data.investigation_trace.forEach(step => {
                                            const li = document.createElement("li");
                                            li.textContent = step.result_summary || `${step.tool} completed`;
                                            investigationList.appendChild(li);
                                        });
                                    }
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
