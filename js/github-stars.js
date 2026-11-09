// GitHub Stars and Forks Button with count
(function() {
  function addGitHubButtons() {
    // Check if buttons already exist
    if (document.querySelector('.github-stats-buttons')) {
      return;
    }

    // Default values in case API fails
    let stars = '0';
    let forks = '0';

    // Format numbers with K suffix if > 1000
    const formatNumber = (num) => {
      if (typeof num === 'string') return num;
      return num >= 1000 ? (num / 1000).toFixed(1) + 'k' : num.toString();
    };

    // Function to create and insert buttons
    const createButtons = (starsCount, forksCount) => {
      const githubButtons = document.createElement('div');
      githubButtons.className = 'github-stats-buttons';
      githubButtons.innerHTML = `
        <a href="https://github.com/inclusionAI/AWorld/fork" target="_blank" rel="noopener" class="github-stat-button">
          <svg class="github-icon" viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M5 5.372v.878c0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75v-.878a2.25 2.25 0 1 1 1.5 0v.878a2.25 2.25 0 0 1-2.25 2.25h-1.5v2.128a2.251 2.251 0 1 1-1.5 0V8.5h-1.5A2.25 2.25 0 0 1 3.5 6.25v-.878a2.25 2.25 0 1 1 1.5 0ZM5 3.25a.75.75 0 1 0-1.5 0 .75.75 0 0 0 1.5 0Zm6.75.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm-3 8.75a.75.75 0 1 0-1.5 0 .75.75 0 0 0 1.5 0Z"></path>
          </svg>
          <span class="github-text">Fork</span>
          <span class="github-count">${formatNumber(forksCount)}</span>
        </a>
        <a href="https://github.com/inclusionAI/AWorld" target="_blank" rel="noopener" class="github-stat-button">
          <svg class="github-icon" viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25Zm0 2.445L6.615 5.5a.75.75 0 0 1-.564.41l-3.097.45 2.24 2.184a.75.75 0 0 1 .216.664l-.528 3.084 2.769-1.456a.75.75 0 0 1 .698 0l2.77 1.456-.53-3.084a.75.75 0 0 1 .216-.664l2.24-2.183-3.096-.45a.75.75 0 0 1-.564-.41L8 2.694Z"></path>
          </svg>
          <span class="github-text">Star</span>
          <span class="github-count">${formatNumber(starsCount)}</span>
        </a>
      `;

      // Insert into header
      const header = document.querySelector('.md-header__inner');
      if (header) {
        header.appendChild(githubButtons);
        console.log('GitHub buttons added successfully');
      } else {
        console.error('Could not find .md-header__inner');
      }
    };

    // First, create buttons with default values immediately
    createButtons(stars, forks);

    // Then try to fetch real data from GitHub API
    const repoUrl = 'https://api.github.com/repos/inclusionAI/AWorld';

    fetch(repoUrl)
      .then(response => {
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        return response.json();
      })
      .then(data => {
        stars = data.stargazers_count || 0;
        forks = data.forks_count || 0;

        // Update the counts in the existing buttons
        const existingButtons = document.querySelector('.github-stats-buttons');
        if (existingButtons) {
          const countElements = existingButtons.querySelectorAll('.github-count');
          if (countElements.length >= 2) {
            countElements[0].textContent = formatNumber(forks); // Fork count
            countElements[1].textContent = formatNumber(stars); // Star count
          }
        }
        console.log(`GitHub stats updated: ${stars} stars, ${forks} forks`);
      })
      .catch(error => {
        console.warn('Could not fetch GitHub stats (using default values):', error.message);
        // Buttons are already displayed with default values, so no action needed
      });
  }

  // Try multiple loading strategies
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addGitHubButtons);
  } else {
    addGitHubButtons();
  }

  // Also try after a short delay for dynamic content
  setTimeout(addGitHubButtons, 100);
  setTimeout(addGitHubButtons, 500);
})();
