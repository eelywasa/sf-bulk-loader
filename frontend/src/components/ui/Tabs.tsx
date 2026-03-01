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
      <TabList className="flex border-b border-gray-200">
        {tabs.map((tab) => (
          <Tab
            key={tab.id}
            disabled={tab.disabled}
            className={({ selected }: { selected: boolean }) =>
              clsx(
                'px-4 py-2.5 text-sm font-medium whitespace-nowrap',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
                'transition-colors duration-150',
                selected
                  ? 'text-blue-600 border-b-2 border-blue-600 -mb-px'
                  : 'text-gray-500 hover:text-gray-700 hover:border-b-2 hover:border-gray-300 -mb-px',
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
