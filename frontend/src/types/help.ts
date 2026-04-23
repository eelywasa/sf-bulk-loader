export interface HelpHeading {
  id: string
  text: string
  level: number
}

export interface HelpTopic {
  slug: string
  title: string
  nav_order: number
  tags: string[]
  summary: string
  required_permission?: string
  html: string
  headings: HelpHeading[]
  bodyText: string
}

export interface HelpContentIndex {
  topics: HelpTopic[]
}
