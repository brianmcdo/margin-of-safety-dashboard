import { HistoricalPage } from "@/components/historical-page"
import { ResearchPage } from "@/components/research-page"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { TooltipProvider } from "@/components/ui/tooltip"

export function App() {
  return (
    <TooltipProvider>
      <div className="min-h-svh bg-background">
        <header className="border-b">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
            <p className="text-sm font-medium">Margin of Safety</p>
            <p className="text-xs text-muted-foreground">Research signals. Not investment advice.</p>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6">
          <Tabs defaultValue="research">
            <TabsList>
              <TabsTrigger value="research">Research</TabsTrigger>
              <TabsTrigger value="historical">Historical</TabsTrigger>
            </TabsList>
            <TabsContent value="research" className="pt-4">
              <ResearchPage />
            </TabsContent>
            <TabsContent value="historical" className="pt-4">
              <HistoricalPage />
            </TabsContent>
          </Tabs>
        </main>
      </div>
    </TooltipProvider>
  )
}

export default App
