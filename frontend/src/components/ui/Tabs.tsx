import React from 'react'
import { Tab, TabGroup, TabList, TabPanel, TabPanels } from '@headlessui/react'
import clsx from 'clsx'

export interface TabItem {
  id: string
  label: string
  content: React.ReactNode
  disabled?: boolean
}

export interface TabsProps {
  tabs: TabItem[]
  className?: string
  defaultIndex?: number
  onChange?: (index: number) => void
}

export function Tabs({ tabs, className, defaultIndex = 0, onChange }: TabsProps) {
  return (
    <TabGroup
      defaultIndex={defaultIndex}
      onChange={onChange}
      className={clsx('w-full', className)}
    >
      <TabList className="flex border-b border-border-base">
        {tabs.map((tab) => (
          <Tab
            key={tab.id}
            disabled={tab.disabled}
            className={({ selected }: { selected: boolean }) =>
              clsx(
                'px-4 py-2.5 text-sm font-medium whitespace-nowrap',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-border-focus',
                'transition-colors duration-150',
                selected
                  ? 'text-accent border-b-2 border-accent -mb-px'
                  : 'text-content-muted hover:text-content-secondary hover:border-b-2 hover:border-border-strong -mb-px',
                tab.disabled && 'opacity-50 cursor-not-allowed',
              )
            }
          >
            {tab.label}
          </Tab>
        ))}
      </TabList>

      <TabPanels className="mt-4">
        {tabs.map((tab) => (
          <TabPanel key={tab.id} className="focus:outline-none">
            {tab.content}
          </TabPanel>
        ))}
      </TabPanels>
    </TabGroup>
  )
}
