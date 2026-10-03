FRAGMENTS = """fragment ActorFields on Actor {
  __typename
  login
}

fragment TimelineFields on PullRequestTimelineItems {
  __typename
  ... on Node { id }
  ... on ReadyForReviewEvent { createdAt actor { ...ActorFields } }
  ... on ConvertToDraftEvent { createdAt actor { ...ActorFields } }
  ... on ReviewRequestedEvent {
    createdAt
    actor { ...ActorFields }
    requestedReviewer { __typename ... on User { login } }
  }
  ... on ReviewRequestRemovedEvent { createdAt actor { ...ActorFields } }
  ... on PullRequestReview { id state submittedAt author { ...ActorFields } }
  ... on ReviewDismissedEvent { createdAt actor { ...ActorFields } previousReviewState review {
    id author { ...ActorFields } } }
  ... on PullRequestCommit { commit { oid authoredDate committedDate messageHeadline messageBody
    } }
  ... on HeadRefForcePushedEvent { createdAt actor { ...ActorFields } }
  ... on IssueComment { createdAt author { ...ActorFields } }
  ... on LabeledEvent { createdAt actor { ...ActorFields } label { name } }
  ... on UnlabeledEvent { createdAt actor { ...ActorFields } label { name } }
  ... on ClosedEvent { createdAt actor { ...ActorFields } }
  ... on ReopenedEvent { createdAt actor { ...ActorFields } }
  ... on MergedEvent { createdAt actor { ...ActorFields } }
  ... on CrossReferencedEvent {
    createdAt
    willCloseTarget
    source {
      __typename
      ... on PullRequest { number state mergedAt author { ...ActorFields } repository {
    nameWithOwner } }
    }
  }
}

"""

PULL_REQUESTS_PAGE = (
    FRAGMENTS
    + """query PullRequestsPage($owner: String!, $name: String!, $pageSize: Int!, $cursor: String,
    $states: [PullRequestState!]) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    nameWithOwner
    isArchived
    defaultBranchRef { name }
    pullRequests(first: $pageSize, after: $cursor, states: $states, orderBy: {field: UPDATED_AT,
    direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        number
        title
        body
        url
        state
        isDraft
        createdAt
        updatedAt
        closedAt
        mergedAt
        additions
        deletions
        changedFiles
        baseRefName
        headRefName
        authorAssociation
        author { ...ActorFields }
        mergedBy { ...ActorFields }
        mergeCommit { oid }
        labels(first: 30) { nodes { name } }
        files(first: 100) { pageInfo { hasNextPage } nodes { path } }
        timelineItems(first: 100, itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT,
    REVIEW_REQUESTED_EVENT, REVIEW_REQUEST_REMOVED_EVENT, PULL_REQUEST_REVIEW,
    REVIEW_DISMISSED_EVENT, PULL_REQUEST_COMMIT, HEAD_REF_FORCE_PUSHED_EVENT, ISSUE_COMMENT,
    LABELED_EVENT, UNLABELED_EVENT, CLOSED_EVENT, REOPENED_EVENT, MERGED_EVENT,
    CROSS_REFERENCED_EVENT]) {
          pageInfo { hasNextPage endCursor }
          nodes { ...TimelineFields }
        }
      }
    }
  }
}

"""
)

PULL_REQUEST_TIMELINE = (
    FRAGMENTS
    + """query PullRequestTimeline($id: ID!, $cursor: String) {
  rateLimit { cost remaining resetAt }
  node(id: $id) {
    ... on PullRequest {
      timelineItems(first: 100, after: $cursor, itemTypes: [READY_FOR_REVIEW_EVENT,
    CONVERT_TO_DRAFT_EVENT, REVIEW_REQUESTED_EVENT, REVIEW_REQUEST_REMOVED_EVENT,
    PULL_REQUEST_REVIEW, REVIEW_DISMISSED_EVENT, PULL_REQUEST_COMMIT,
    HEAD_REF_FORCE_PUSHED_EVENT, ISSUE_COMMENT, LABELED_EVENT, UNLABELED_EVENT, CLOSED_EVENT,
    REOPENED_EVENT, MERGED_EVENT, CROSS_REFERENCED_EVENT]) {
        pageInfo { hasNextPage endCursor }
        nodes { ...TimelineFields }
      }
    }
  }
}
"""
)
