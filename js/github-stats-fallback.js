// GitHub Stats Fallback - Uses shields.io badges as backup
(function() {
  // This runs if the main github-stars.js fails
  setTimeout(function() {
    const existingButtons = document.querySelector('.github-stats-buttons');

    // Only add fallback if main script didn't work
    if (!existingButtons) {
      console.log('Adding fallback GitHub buttons');

      const githubButtons = document.createElement('div');
      githubButtons.className = 'github-stats-buttons';
      githubButtons.innerHTML = `
        <a href="https://github.com/inclusionAI/AWorld/fork" target="_blank" rel="noopener" class="github-stat-button">
          <svg class="github-icon" viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M5 5.372v.878c0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75v-.878a2.25 2.25 0 1 1 1.5 0v.878a2.25 2.25 0 0 1-2.25 2.25h-1.5v2.128a2.251 2.251 0 1 1-1.5 0V8.5h-1.5A2.25 2.25 0 0 1 3.5 6.25v-.878a2.25 2.25 0 1 1 1.5 0ZM5 3.25a.75.75 0 1 0-1.5 0 .75.75 0 0 0 1.5 0Zm6.75.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm-3 8.75a.75.75 0 1 0-1.5 0 .75.75 0 0 0 1.5 0Z"></path>
          </svg>
          <span class="github-text">Fork</span>
          <span class="github-count">-</span>
        </a>
        <a href="https://github.com/inclusionAI/AWorld" target="_blank" rel="noopener" class="github-stat-button">
          <svg class="github-icon" viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25Zm0 2.445L6.615 5.5a.75.75 0 0 1-.564.41l-3.097.45 2.24 2.184a.75.75 0 0 1 .216.664l-.528 3.084 2.769-1.456a.75.75 0 0 1 .698 0l2.77 1.456-.53-3.084a.75.75 0 0 1 .216-.664l2.24-2.183-3.096-.45a.75.75 0 0 1-.564-.41L8 2.694Z"></path>
          </svg>
          <span class="github-text">Star</span>
          <span class="github-count">-</span>
        </a>
      `;

      const header = document.querySelector('.md-header__inner');
      if (header) {
        header.appendChild(githubButtons);

        // Try to fetch counts using shields.io API
        fetch('https://img.shields.io/github/stars/inclusionAI/AWorld?style=social')
          .then(() => {
            // If shields.io works, use ungh.cc API as alternative
            return fetch('https://ungh.cc/repos/inclusionAI/AWorld');
          })
          .then(response => response.json())
          .then(data => {
            if (data && data.repo) {
              const stars = data.repo.stars || '-';
              const forks = data.repo.forks || '-';
              const counts = githubButtons.querySelectorAll('.github-count');
              if (counts.length >= 2) {
                counts[0].textContent = forks;
                counts[1].textContent = stars;
              }
            }
          })
          .catch(err => console.warn('Fallback API also failed:', err));
      }
    } else {
      // Check if counts are still at default, try to update them
      const counts = existingButtons.querySelectorAll('.github-count');
      if (counts.length >= 2 && counts[0].textContent === '0') {
        // Try alternative API
        fetch('https://ungh.cc/repos/inclusionAI/AWorld')
          .then(response => response.json())
          .then(data => {
            if (data && data.repo) {
              const stars = data.repo.stars || '0';
              const forks = data.repo.forks || '0';
              counts[0].textContent = forks;
              counts[1].textContent = stars;
              console.log('Updated counts from fallback API');
            }
          })
          .catch(err => console.warn('Could not update from fallback API:', err));
      }
    }
  }, 2000);
})();
