# Example Nano Banana Pro Prompts

Use these as references for tone, structure, and level of detail. Adapt the zones
and resources to the parsed architecture. See `visual-style.md` (next to this file, under `references/`) for the full
design system these prompts follow.

## Three-Tier Web Application

```
A professional 16:9 landscape cloud architecture diagram in a stunning modern infographic style.

CANVAS AND MARGINS:
The image has a generous white outer margin (at least 60 pixels on all sides) so no content approaches the page edges. Inside this margin, a subtle rounded-corner border with a thin charcoal stroke frames the entire diagram. All content is contained inside this border with comfortable inner padding.

HEADER (inside the frame):
A gradient header bar spans the top inside the frame, transitioning from deep navy blue on the left to teal on the right. The title 'Three-Tier Web Application' appears in large, bold white sans-serif text centered in the header. A subtitle below reads 'AWS Production Environment • us-east-1' in smaller light blue text.

MAIN CANVAS:
The main area has a very light cool gray background. The layout flows left-to-right showing the request path from internet to database. Generous spacing between all elements.

VISUAL STYLE:
All icons are rendered in a clean isometric 3D style with subtle drop shadows and soft gradient fills. Icons are crisp, detailed, and visually appealing like high-end tech infographic illustrations. Each resource type uses harmonious colors from a modern palette.

ARCHITECTURE ZONES (arranged left to right):

ZONE 1 - Internet Entry (far left):
A small cloud icon labeled 'Internet' with a globe symbol. A bright blue curved arrow flows rightward.

ZONE 2 - Public Layer (light blue tinted rectangle with dashed blue border, labeled 'Public Subnet 10.0.1.0/24'):
- A purple isometric Internet Gateway icon with network symbol
- A purple isometric Application Load Balancer icon labeled 'web-alb' with a circular distribution symbol
- Blue curved arrows connect them showing HTTPS flow

ZONE 3 - Application Layer (light green tinted rectangle with dashed green border, labeled 'Private Subnet 10.0.2.0/24'):
- Three orange isometric EC2 server icons arranged in a clean row, each labeled 'web-1', 'web-2', 'web-3'
- The servers are grouped within a subtle dotted security boundary labeled 'web-sg'
- Purple curved arrows from the load balancer fan out to each server

ZONE 4 - Data Layer (light amber tinted rectangle with dashed amber border, labeled 'Database Subnet 10.0.3.0/24'):
- A blue isometric RDS database cylinder icon with a subtle glow, labeled 'PostgreSQL Primary'
- A smaller replica icon labeled 'Read Replica' below it
- Amber dashed curved arrows connect from the EC2 instances with pill-shaped labels reading 'PostgreSQL 5432'

FOOTER (inside the frame, at the bottom):
A thin footer bar inside the border frame contains a compact legend showing icon types with labels. The AWS logo appears discretely in the bottom-right corner, also inside the frame. Everything is well within the border with no content touching or extending beyond it.

The overall aesthetic is clean, modern, and visually stunning - suitable for executive presentations and technical documentation alike.
```

## Microservices on Kubernetes

```
A professional 16:9 landscape Kubernetes architecture diagram in a stunning modern infographic style.

CANVAS AND MARGINS:
The image has a generous white outer margin (at least 60 pixels on all sides) so no content approaches the page edges. Inside this margin, a subtle rounded-corner border with a thin charcoal stroke frames the entire diagram. All content is contained inside this border.

HEADER (inside the frame):
A gradient header bar spans the top inside the frame, transitioning from deep indigo on the left to violet on the right. The title 'Microservices on Kubernetes' appears in large, bold white sans-serif text. A subtitle reads 'EKS Production Cluster • Multi-Namespace Architecture' in smaller light purple text.

MAIN CANVAS:
Light cool gray background (#f8fafc). The diagram represents a Kubernetes cluster as a large rounded rectangle with a subtle shadow. Generous spacing between elements.

VISUAL STYLE:
All icons are clean isometric 3D with subtle shadows and modern gradient fills. The Kubernetes wheel logo appears subtly watermarked in the cluster background.

CLUSTER BOUNDARY:
A large rounded rectangle with a thin purple dashed border labeled 'EKS Cluster: k8s-prod' in the top-left corner.

INGRESS (top of cluster):
A purple isometric ingress controller icon labeled 'nginx-ingress' sits at the top center. A blue curved arrow enters from above, originating from a cloud/globe icon labeled 'Internet'.

NAMESPACE ZONES (arranged horizontally inside the cluster):

ZONE 1 - Frontend Namespace (light blue tinted rectangle with rounded corners):
- Header label: 'namespace: frontend'
- A purple Service icon labeled 'frontend-svc' at the top
- Three orange Pod icons in a row below, each labeled 'web-app'
- Pods connected to the service with thin lines

ZONE 2 - Backend Namespace (light green tinted rectangle):
- Header label: 'namespace: backend'
- A purple Service icon labeled 'api-svc'
- Three orange Pod icons labeled 'api-server'
- Two teal Pod icons labeled 'worker' below
- Internal green dotted arrows show async messaging

ZONE 3 - Data Namespace (light amber tinted rectangle):
- Header label: 'namespace: data'
- A blue isometric StatefulSet icon labeled 'postgres'
- A green PVC icon labeled 'db-storage' connected below

CONNECTIONS:
- Bright blue curved arrow with 'HTTPS 443' label from ingress to frontend service
- Purple curved arrows with 'HTTP 8080' labels from frontend pods to api-svc
- Amber dashed arrows with 'PostgreSQL 5432' labels from api pods to postgres

EXTERNAL SERVICES (outside cluster, right side):
- A green isometric S3 bucket icon labeled 'user-uploads'
- A blue RDS icon labeled 'analytics-db'
- Green dotted arrows connect worker pods to S3, amber dashed arrows connect api pods to RDS

FOOTER (inside the frame, at the bottom):
A compact footer bar inside the border frame shows a legend with icon samples and labels. Kubernetes logo on left, AWS logo on right - all inside the frame with no content touching the border edge.

The diagram is visually polished with consistent spacing, harmonious colors, generous margins, and professional aesthetics suitable for architecture review presentations.
```

## Quick Prompt Template

Fill in the bracketed sections:

```
A professional 16:9 landscape cloud architecture diagram in a stunning modern infographic style.

CANVAS AND MARGINS:
The image has a generous white outer margin (at least 60 pixels on all sides) so no content approaches the page edges. Inside this margin, a subtle rounded-corner border with a thin charcoal stroke frames all diagram content. Everything is contained inside this border with comfortable inner padding.

HEADER (inside the frame):
A gradient header bar spans the top inside the frame, transitioning from [PRIMARY_COLOR] on the left to [SECONDARY_COLOR] on the right. The title '[ARCHITECTURE_NAME]' appears in large, bold white sans-serif text. A subtitle reads '[DESCRIPTION] • [REGION/ENVIRONMENT]' in smaller light text.

MAIN CANVAS:
Light cool gray background (#f8fafc). Layout flows left-to-right showing the data/request path. Generous spacing between all elements.

VISUAL STYLE:
All icons are clean isometric 3D with subtle drop shadows and soft gradient fills - like high-end tech infographic illustrations. Consistent color palette: orange for compute, purple for networking, blue for databases, green for storage, teal for analytics.

ARCHITECTURE ZONES:
[Describe each zone with tinted background color, dashed border, label, and contained resources]

ZONE 1 - [ZONE_NAME] ([ZONE_COLOR] tinted rectangle with dashed border):
- [Resource descriptions with isometric style, color, and labels]

ZONE 2 - [ZONE_NAME] ([ZONE_COLOR] tinted rectangle):
- [Resource descriptions]

[Continue for additional zones...]

CONNECTIONS:
[Describe each connection with curved bezier arrows, color, and pill-shaped labels]
- [COLOR] curved arrow with '[PROTOCOL PORT]' label from [SOURCE] to [DESTINATION]

FOOTER (inside the frame, at the bottom):
A compact footer bar inside the border frame shows a legend with icon samples and labels. [PROVIDER] logo in bottom-right corner - all inside the frame with no content touching or extending beyond the border.

The diagram is visually polished with generous margins, consistent spacing, harmonious colors, and professional aesthetics.
```

## Color suggestions for headers

- AWS: Navy blue (#1a365d) → Teal (#0d9488)
- Azure: Dark blue (#1e3a5f) → Cyan (#06b6d4)
- GCP: Deep blue (#1e40af) → Red (#dc2626)
- Kubernetes: Indigo (#3730a3) → Violet (#7c3aed)
- Multi-cloud: Slate (#334155) → Purple (#9333ea)
