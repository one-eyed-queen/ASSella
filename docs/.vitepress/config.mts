import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'EXE-fingerprinter',
  description: 'Fingerprint a Steam game and download just its .exe files.',
  base: '/',
  cleanUrls: true,
  ignoreDeadLinks: true,
  head: [
    ['link', { rel: 'icon', type: 'image/png', href: '/logo.png' }]
  ],
  themeConfig: {
    logo: '/logo.png',
    siteTitle: 'EXE-fingerprinter',
    nav: [
      { text: 'Installation', link: '/install' },
      { text: 'How it works', link: '/downloading' },
      { text: 'Releases', link: 'https://github.com/niwia/ASSella/releases' }
    ],
    sidebar: [
      {
        text: 'EXE-fingerprinter',
        items: [
          { text: 'Installation', link: '/install' },
          { text: 'How it works', link: '/downloading' },
          { text: 'Changes', link: '/changelogs/3.0.0' }
        ]
      }
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/niwia/ASSella' }
    ],
    search: {
      provider: 'local'
    },
    footer: {
      message: 'EXE-fingerprinter — a TUI that downloads only the .exe files of Steam games.',
      copyright: 'GPL-3.0'
    }
  }
})
